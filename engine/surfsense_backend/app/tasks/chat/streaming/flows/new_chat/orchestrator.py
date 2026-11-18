"""``stream_new_chat`` — public entry point for a fresh chat turn.

Slim composition layer over the per-concern modules in this folder and the
building blocks under ``flows/shared/``. Each phase corresponds to a numbered
block in the surrounding code so the on-the-wire ordering stays explicit:

  1. Validation / config — auto-pin, LLM bundle, capability.
  2. Concurrent persistence + pre-stream setup — spawn DB writes, build the
     connector, fetch the checkpointer, build the agent.
  3. Input assembly — history bootstrap, mentions, surfsense docs, reports.
  4. First SSE frames — message_start, start_step, turn-info, turn-status.
  5. Persistence join + message-id frames (ghost-thread protection).
  6. Title task + runtime context.
  7. Stream loop with in-stream rate-limit recovery + mid-stream title emit.
  8. Finalize — token-usage SSE, finish frames.
  9. Exception branch — classify, emit terminal error, finish frames.
 10. Finally — session close, assistant finalize, GC, span.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from functools import partial
from typing import Any, Literal

import anyio

from app.agents.chat.multi_agent_chat import create_multi_agent_chat_deep_agent
from app.agents.chat.retrieval_scope import RetrievalScope
from app.auth.context import AuthContext
from app.db import ChatVisibility, async_session_maker
from app.observability.signals import tracing
from app.services.new_streaming_service import VercelStreamingService
from app.tasks.chat.content_builder import AssistantContentBuilder
from app.tasks.chat.streaming.agent.builder import build_main_agent_for_thread
from app.tasks.chat.streaming.errors.emitter import emit_stream_terminal_error
from app.tasks.chat.streaming.flows.new_chat.auto_pin import resolve_initial_auto_pin
from app.tasks.chat.streaming.flows.new_chat.input_state import (
    build_new_chat_input_state,
)
from app.tasks.chat.streaming.flows.new_chat.llm_capability import (
    check_image_input_capability,
)
from app.tasks.chat.streaming.flows.new_chat.persistence_spawn import (
    await_persist_task,
    spawn_persist_assistant_shell_task,
    spawn_persist_user_task,
    spawn_set_ai_responding_bg,
)
from app.tasks.chat.streaming.flows.new_chat.runtime_context import (
    build_new_chat_runtime_context,
)
from app.tasks.chat.streaming.flows.new_chat.title_gen import (
    await_pending_title_update,
    maybe_emit_title_update,
    spawn_title_task,
)
from app.tasks.chat.streaming.flows.shared.finalize_emit import iter_token_usage_frame
from app.tasks.chat.streaming.flows.shared.first_frames import (
    iter_final_frames,
    iter_initial_frames,
)
from app.tasks.chat.streaming.flows.shared.llm_bundle import load_llm_bundle
from app.tasks.chat.streaming.flows.shared.pre_stream_setup import (
    get_chat_checkpointer,
    setup_connector_service,
)
from app.tasks.chat.streaming.flows.shared.rate_limit_recovery import (
    can_recover_provider_rate_limit,
    log_rate_limit_recovered,
    reroute_to_next_auto_pin,
)
from app.tasks.chat.streaming.flows.shared.span import (
    set_agent_mode,
)
from app.tasks.chat.streaming.flows.shared.stream_loop import run_stream_loop
from app.tasks.chat.streaming.flows.shared.turn import (
    begin_turn,
    finish_turn,
    iter_terminal_exception_frames,
    release_turn_lock,
    run_shielded_turn_cleanup,
)
from app.tasks.chat.streaming.relay.activity_sse import (
    emit_activity_timing_frame,
    emit_completed_activity_timing_frame,
)
from app.utils.perf import get_perf_logger, log_system_snapshot

logger = logging.getLogger(__name__)
_perf_log = get_perf_logger()

# Holds spawned background tasks (set_ai_responding, persist_user, persist_asst)
# so the GC doesn't drop them before they finish. Kept at module level so it
# survives across turns within one process.
_background_tasks: set[asyncio.Task] = set()


async def stream_new_chat(
    user_query: str,
    workspace_id: int,
    chat_id: int,
    user_id: str | None = None,
    llm_config_id: int = -1,
    mentioned_document_ids: list[int] | None = None,
    mentioned_folder_ids: list[int] | None = None,
    mentioned_connector_ids: list[int] | None = None,
    mentioned_connectors: list[dict[str, Any]] | None = None,
    mentioned_documents: list[dict[str, Any]] | None = None,
    mentioned_thread_ids: list[int] | None = None,
    checkpoint_id: str | None = None,
    needs_history_bootstrap: bool = False,
    thread_visibility: ChatVisibility | None = None,
    current_user_display_name: str | None = None,
    disabled_tools: list[str] | None = None,
    request_id: str | None = None,
    user_image_data_urls: list[str] | None = None,
    auth_context: AuthContext | None = None,
    flow: Literal["new", "regenerate"] = "new",
    retrieval_scope: RetrievalScope = RetrievalScope.DOCUMENTS,
) -> AsyncGenerator[str, None]:
    """Stream a new chat turn using the SurfSense deep agent.

    Uses the Vercel AI SDK Data Stream Protocol (SSE). ``chat_id`` is the
    LangGraph thread id (durable conversation memory via the checkpointer).
    Manages its own database session so cleanup runs even when Starlette
    cancels the task on client disconnect.
    """
    streaming_service = VercelStreamingService()
    turn, stream_result = begin_turn(
        flow=flow,
        chat_id=chat_id,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request_id,
        auth_context=auth_context,
        log_prefix="stream_new_chat",
        error_log_prefix="stream_new_chat",
    )
    log_system_snapshot("stream_new_chat_START")

    from app.agents.chat.multi_agent_chat.shared.web_budget import start_turn_budget
    from app.services.token_tracking_service import start_turn

    accumulator = start_turn()
    # One hard web-search budget per reply, shared by every specialist.
    start_turn_budget()

    emit_stream_error = partial(
        emit_stream_terminal_error,
        streaming_service=streaming_service,
        flow=flow,
        request_id=request_id,
        thread_id=chat_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )

    session = async_session_maker()
    # Declared at function scope so SSE-yield join points and the finally
    # clause see them on every exit path.
    persist_user_task: asyncio.Task[int | None] | None = None
    persist_asst_task: asyncio.Task[int | None] | None = None
    try:
        spawn_set_ai_responding_bg(
            chat_id=chat_id, user_id=user_id, background_tasks=_background_tasks
        )

        # --- Block 1: LLM config + capability ---

        requested_llm_config_id = llm_config_id
        requires_image_input = bool(user_image_data_urls)

        _t0 = time.perf_counter()
        pin_result = await resolve_initial_auto_pin(
            session,
            chat_id=chat_id,
            workspace_id=workspace_id,
            user_id=user_id,
            selected_llm_config_id=llm_config_id,
            requires_image_input=requires_image_input,
            requested_llm_config_id=requested_llm_config_id,
        )
        if pin_result.error is not None:
            error_code, error_kind, diagnostic = pin_result.error
            yield emit_stream_error(
                error_kind=error_kind,
                error_code=error_code,
                diagnostic=diagnostic,
            )
            yield streaming_service.format_done()
            return
        llm_config_id = pin_result.llm_config_id  # type: ignore[assignment]

        llm, agent_config, llm_load_error = await load_llm_bundle(
            session, config_id=llm_config_id, workspace_id=workspace_id
        )
        if llm_load_error:
            yield emit_stream_error(
                error_kind="server_error",
                error_code="SERVER_ERROR",
                diagnostic=llm_load_error,
            )
            yield streaming_service.format_done()
            return
        _perf_log.info(
            "[stream_new_chat] LLM config loaded in %.3fs (config_id=%s)",
            time.perf_counter() - _t0,
            llm_config_id,
        )

        capability_error = check_image_input_capability(
            user_image_data_urls=user_image_data_urls, agent_config=agent_config
        )
        if capability_error is not None:
            yield emit_stream_error(
                error_kind="user_error",
                error_code=capability_error,
            )
            yield streaming_service.format_done()
            return

        if not llm:
            yield emit_stream_error(
                error_kind="server_error",
                error_code="SERVER_ERROR",
                diagnostic="Failed to create LLM instance",
            )
            yield streaming_service.format_done()
            return

        # --- Block 2: Spawn concurrent persistence; build pre-stream setup ---

        persist_user_task = spawn_persist_user_task(
            chat_id=chat_id,
            user_id=user_id,
            turn_id=stream_result.turn_id,
            user_query=user_query,
            user_image_data_urls=user_image_data_urls,
            mentioned_documents=mentioned_documents,
            background_tasks=_background_tasks,
        )

        _t0 = time.perf_counter()
        connector_service = await setup_connector_service(
            session, workspace_id=workspace_id
        )
        _perf_log.info(
            "[stream_new_chat] Connector service in %.3fs",
            time.perf_counter() - _t0,
        )

        _t0 = time.perf_counter()
        checkpointer = await get_chat_checkpointer()
        _perf_log.info(
            "[stream_new_chat] Checkpointer ready in %.3fs", time.perf_counter() - _t0
        )

        visibility = thread_visibility or ChatVisibility.PRIVATE
        turn.agent_mode = "multi"
        set_agent_mode(turn.span, turn.agent_mode)

        _t0 = time.perf_counter()
        agent_factory = create_multi_agent_chat_deep_agent
        # Build the agent inline. Provider 429s surface through the in-stream
        # recovery loop below, which repins the thread to an eligible
        # alternative config and rebuilds the agent before the user sees any
        # output.
        agent = await build_main_agent_for_thread(
            agent_factory,
            llm=llm,
            workspace_id=workspace_id,
            db_session=session,
            connector_service=connector_service,
            checkpointer=checkpointer,
            user_id=user_id,
            thread_id=chat_id,
            agent_config=agent_config,
            thread_visibility=visibility,
            disabled_tools=disabled_tools,
            mentioned_document_ids=mentioned_document_ids,
            auth_context=auth_context,
            retrieval_scope=retrieval_scope,
        )
        _perf_log.info(
            "[stream_new_chat] Agent created in %.3fs", time.perf_counter() - _t0
        )

        # --- Block 3: Input assembly ---

        _t0 = time.perf_counter()
        assembled = await build_new_chat_input_state(
            session,
            chat_id=chat_id,
            workspace_id=workspace_id,
            user_query=user_query,
            user_image_data_urls=user_image_data_urls,
            mentioned_document_ids=mentioned_document_ids,
            mentioned_folder_ids=mentioned_folder_ids,
            mentioned_connectors=mentioned_connectors,
            mentioned_documents=mentioned_documents,
            mentioned_thread_ids=mentioned_thread_ids,
            requesting_user_id=user_id,
            needs_history_bootstrap=needs_history_bootstrap,
            thread_visibility=visibility,
            current_user_display_name=current_user_display_name,
            request_id=request_id,
            turn_id=stream_result.turn_id,
        )
        input_state = assembled.input_state
        accepted_folder_ids = assembled.accepted_folder_ids
        _perf_log.info(
            "[stream_new_chat] History bootstrap + doc/report queries in %.3fs",
            time.perf_counter() - _t0,
        )

        # All pre-streaming DB reads done. Commit to release the transaction
        # and its ACCESS SHARE locks so we don't block DDL (e.g. migrations)
        # for the entire LLM streaming duration. Tools that need DB access
        # during streaming start their own short-lived transactions (or use
        # isolated sessions).
        await session.commit()
        # Detach heavy ORM objects (documents with chunks, reports, etc.)
        # from the session identity map now that we've extracted what we
        # need. Without this they accumulate in memory for the entire
        # streaming duration (which can be several minutes).
        session.expunge_all()

        _perf_log.info(
            "[stream_new_chat] Total pre-stream setup in %.3fs (chat_id=%s)",
            time.perf_counter() - turn.t_total,
            chat_id,
        )

        configurable: dict[str, Any] = {
            "thread_id": str(chat_id),
            "request_id": request_id or "unknown",
            "turn_id": stream_result.turn_id,
        }
        if checkpoint_id:
            configurable["checkpoint_id"] = checkpoint_id

        config = {
            "configurable": configurable,
            # Effectively uncapped, matching the agent-level ``with_config``
            # default in ``chat_deepagent.create_agent`` and the unbounded
            # ``while(true)`` in OpenCode's ``session/processor.ts``. Real
            # circuit-breakers live in middleware (``DoomLoopMiddleware``,
            # plus ``enable_tool_call_limit`` / ``enable_model_call_limit``).
            # The original 25 (and our previous 80 bump) hit users on
            # legitimate multi-tool plans.
            "recursion_limit": 10_000,
        }

        # --- Block 4: First SSE frames ---

        for sse in iter_initial_frames(
            streaming_service, turn_id=stream_result.turn_id
        ):
            yield sse

        # --- Block 5: Persistence join + message-id frames ---

        user_message_id = await await_persist_task(
            persist_user_task,
            chat_id=chat_id,
            turn_id=stream_result.turn_id,
            log_label="persist_user_task",
        )
        if user_message_id is None:
            yield emit_stream_error(
                error_kind="server_error",
                error_code="MESSAGE_PERSIST_FAILED",
            )
            for sse in iter_final_frames(streaming_service):
                yield sse
            return

        # Emit canonical user message id BEFORE any LLM streaming so the FE
        # can rename its optimistic ``msg-user-XXX`` placeholder to
        # ``msg-{user_message_id}`` and unlock features gated on a real DB id
        # (comments, edit-from-this-message). See B4 in the
        # ``sse-based_message_id_handshake`` plan.
        yield streaming_service.format_data(
            "user-message-id",
            {"message_id": user_message_id, "turn_id": stream_result.turn_id},
        )

        # Spawned only after the user row is confirmed, so a user-persist
        # failure can't orphan an assistant shell on the same turn.
        persist_asst_task = spawn_persist_assistant_shell_task(
            chat_id=chat_id,
            user_id=user_id,
            turn_id=stream_result.turn_id,
            background_tasks=_background_tasks,
        )
        assistant_message_id = await await_persist_task(
            persist_asst_task,
            chat_id=chat_id,
            turn_id=stream_result.turn_id,
            log_label="persist_asst_task",
        )
        if assistant_message_id is None:
            # Genuine DB failure — abort the turn rather than stream into a
            # void. The user row is already persisted so the legacy
            # ghost-thread gate isn't reopened.
            yield emit_stream_error(
                error_kind="server_error",
                error_code="MESSAGE_PERSIST_FAILED",
            )
            for sse in iter_final_frames(streaming_service):
                yield sse
            return

        yield streaming_service.format_data(
            "assistant-message-id",
            {"message_id": assistant_message_id, "turn_id": stream_result.turn_id},
        )

        stream_result.assistant_message_id = assistant_message_id
        stream_result.content_builder = AssistantContentBuilder()
        yield emit_activity_timing_frame(
            streaming_service=streaming_service,
            content_builder=stream_result.content_builder,
            snapshot=stream_result.activity_timer.snapshot(),
        )

        # --- Block 6: Title task + runtime context ---
        # Drop the heavy ORM objects + the container that holds them so they
        # aren't retained for the entire streaming duration. ``input_state``
        # already carries the langchain_messages list independently.
        del assembled

        title_task = spawn_title_task(
            chat_id=chat_id,
            user_query=user_query,
            user_image_data_urls=user_image_data_urls,
            assistant_message_id=assistant_message_id,
            llm=llm,
            agent_config=agent_config,
        )
        title_emitted = False

        runtime_context = build_new_chat_runtime_context(
            workspace_id=workspace_id,
            mentioned_document_ids=mentioned_document_ids,
            accepted_folder_ids=accepted_folder_ids,
            mentioned_folder_ids=mentioned_folder_ids,
            mentioned_connector_ids=mentioned_connector_ids,
            mentioned_connectors=mentioned_connectors,
            request_id=request_id,
            turn_id=stream_result.turn_id,
        )

        # --- Block 7: Stream loop ---

        _t_stream_start = time.perf_counter()
        runtime_rate_limit_recovered = False

        def _on_first_event() -> None:
            _perf_log.info(
                "[stream_new_chat] First agent event in %.3fs (time since stream start), "
                "%.3fs (total since request start) (chat_id=%s)",
                time.perf_counter() - _t_stream_start,
                time.perf_counter() - turn.t_total,
                chat_id,
            )

        async def _recover(exc: BaseException, first_event_seen: bool):
            nonlocal llm_config_id, llm, agent_config, runtime_rate_limit_recovered
            nonlocal title_task
            if not can_recover_provider_rate_limit(
                exc,
                first_event_seen=first_event_seen,
                runtime_rate_limit_recovered=runtime_rate_limit_recovered,
                requested_llm_config_id=requested_llm_config_id,
                current_llm_config_id=llm_config_id,
            ):
                return None
            runtime_rate_limit_recovered = True
            previous_config_id = llm_config_id
            llm_config_id = await reroute_to_next_auto_pin(
                session,
                chat_id=chat_id,
                workspace_id=workspace_id,
                user_id=user_id,
                current_llm_config_id=llm_config_id,
                requires_image_input=requires_image_input,
            )
            new_llm, new_agent_config, llm_load_err = await load_llm_bundle(
                session, config_id=llm_config_id, workspace_id=workspace_id
            )
            if llm_load_err:
                # Re-raise the original so the terminal-error path classifies
                # it correctly (don't swallow as "config load error").
                return None
            llm = new_llm
            agent_config = new_agent_config

            # Title gen used the initial llm object. After a runtime repin we
            # keep the stream focused on response recovery and skip title gen
            # for this turn.
            if title_task is not None and not title_task.done():
                title_task.cancel()
            title_task = None

            _t_rebuild = time.perf_counter()
            new_agent = await build_main_agent_for_thread(
                agent_factory,
                llm=llm,
                workspace_id=workspace_id,
                db_session=session,
                connector_service=connector_service,
                checkpointer=checkpointer,
                user_id=user_id,
                thread_id=chat_id,
                agent_config=agent_config,
                thread_visibility=visibility,
                disabled_tools=disabled_tools,
                mentioned_document_ids=mentioned_document_ids,
                auth_context=auth_context,
                retrieval_scope=retrieval_scope,
            )
            _perf_log.info(
                "[stream_new_chat] Runtime rate-limit recovery repinned "
                "config_id=%s -> %s and rebuilt agent in %.3fs",
                previous_config_id,
                llm_config_id,
                time.perf_counter() - _t_rebuild,
            )
            log_rate_limit_recovered(
                flow=flow,
                request_id=request_id,
                chat_id=chat_id,
                workspace_id=workspace_id,
                user_id=user_id,
                previous_config_id=previous_config_id,
                new_config_id=llm_config_id,
            )
            return new_agent

        async for sse in run_stream_loop(
            agent=agent,
            streaming_service=streaming_service,
            config=config,
            input_data=input_state,
            stream_result=stream_result,
            step_prefix="turn",
            fallback_commit_workspace_id=workspace_id,
            fallback_commit_created_by_id=user_id,
            fallback_commit_thread_id=chat_id,
            runtime_context=runtime_context,
            content_builder=stream_result.content_builder,
            recover=_recover,
            on_first_event=_on_first_event,
        ):
            yield sse
            # Inject the title update mid-stream as soon as the background
            # task finishes; gated so we emit at most once.
            async for title_sse in maybe_emit_title_update(
                title_task=title_task,
                title_emitted=title_emitted,
                chat_id=chat_id,
                accumulator=accumulator,
                streaming_service=streaming_service,
            ):
                yield title_sse
                title_emitted = True
            # Account for the case where the task completed but produced no
            # title — flip the flag anyway so we don't keep checking it.
            if title_task is not None and title_task.done() and not title_emitted:
                title_emitted = True

        _perf_log.info(
            "[stream_new_chat] Agent stream completed in %.3fs (chat_id=%s)",
            time.perf_counter() - _t_stream_start,
            chat_id,
        )
        log_system_snapshot("stream_new_chat_END")

        # --- Block 8: Finalize ---

        if stream_result.is_interrupted:
            tracing.add_event("chat.interrupted", {"chat.flow": flow})
            if title_task is not None and not title_task.done():
                title_task.cancel()
            for sse in iter_token_usage_frame(
                streaming_service,
                accumulator=accumulator,
                log_label="interrupted new_chat",
            ):
                yield sse
            for sse in iter_final_frames(streaming_service):
                yield sse
            return

        yield emit_completed_activity_timing_frame(
            streaming_service=streaming_service,
            content_builder=stream_result.content_builder,
            timer=stream_result.activity_timer,
        )

        async for title_sse in await_pending_title_update(
            title_task=title_task,
            title_emitted=title_emitted,
            chat_id=chat_id,
            accumulator=accumulator,
            streaming_service=streaming_service,
        ):
            yield title_sse

        for sse in iter_token_usage_frame(
            streaming_service, accumulator=accumulator, log_label="normal new_chat"
        ):
            yield sse

        for sse in iter_final_frames(streaming_service):
            yield sse

    except Exception as exc:
        for sse in iter_terminal_exception_frames(
            turn,
            exc,
            streaming_service=streaming_service,
            stream_result=stream_result,
            flow_label="chat",
        ):
            yield sse

    finally:
        await run_shielded_turn_cleanup(
            turn, session=session, stream_result=stream_result, accumulator=accumulator
        )

        # Persist legacy sandbox-produced files locally before the selected
        # provider session is terminated.
        if stream_result and stream_result.sandbox_files:
            with contextlib.suppress(Exception):
                from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.sandbox import (
                    persist_and_delete_sandbox,
                )
                from app.sandbox import is_sandbox_enabled

                if is_sandbox_enabled():
                    with anyio.CancelScope(shield=True):
                        await persist_and_delete_sandbox(
                            chat_id, stream_result.sandbox_files
                        )

        release_turn_lock(turn)

        # Break circular refs held by the agent graph, tools, and LLM
        # wrappers so the GC can reclaim them in a single pass.
        agent = llm = connector_service = None
        input_state = stream_result = None
        session = None

        finish_turn(turn)
