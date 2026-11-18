"""``stream_resume_chat`` — public entry point for a HITL resume turn.

Slim composition layer over the per-concern modules in this folder and the
building blocks under ``flows/shared/``. Mirrors ``stream_new_chat`` but:

  * No user-message persistence (the original turn already wrote it).
  * No mentions / surfsense-doc / report context assembly (seeded by original).
  * No title generation (only fires on first-response).
  * Synchronous ``persist_assistant_shell`` call (we have no other in-flight
    pre-stream work to overlap it with).
  * ``input_data`` is a ``Command(resume=lg_resume_map)`` instead of a
    LangChain message list.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from functools import partial
from uuid import UUID

from app.agents.chat.multi_agent_chat import create_multi_agent_chat_deep_agent
from app.agents.chat.retrieval_scope import RetrievalScope
from app.auth.context import AuthContext
from app.db import ChatVisibility, async_session_maker
from app.observability.signals import tracing
from app.services.chat_session_state_service import set_ai_responding
from app.services.new_streaming_service import VercelStreamingService
from app.tasks.chat.content_builder import AssistantContentBuilder
from app.tasks.chat.streaming.activity_timing import ActivityTimer
from app.tasks.chat.streaming.agent.builder import build_main_agent_for_thread
from app.tasks.chat.streaming.errors.emitter import emit_stream_terminal_error
from app.tasks.chat.streaming.flows.resume_chat.assistant_shell import (
    load_resumable_activity_journal,
    order_resume_tool_call_ids,
    persist_resume_assistant_shell,
)
from app.tasks.chat.streaming.flows.resume_chat.resume_routing import (
    build_resume_routing,
)
from app.tasks.chat.streaming.flows.resume_chat.runtime_context import (
    build_resume_chat_runtime_context,
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
from app.tasks.chat.streaming.shared.utils import resume_step_prefix
from app.utils.perf import get_perf_logger

logger = logging.getLogger(__name__)
_perf_log = get_perf_logger()


async def stream_resume_chat(
    chat_id: int,
    workspace_id: int,
    decisions: list[dict],
    user_id: str | None = None,
    llm_config_id: int = -1,
    thread_visibility: ChatVisibility | None = None,
    request_id: str | None = None,
    disabled_tools: list[str] | None = None,
    auth_context: AuthContext | None = None,
    retrieval_scope: RetrievalScope = RetrievalScope.DOCUMENTS,
) -> AsyncGenerator[str, None]:
    """Resume a paused HITL turn with the user's decisions.

    Mirrors ``stream_new_chat`` except for the resume-specific routing of
    ``decisions`` to per-``tool_call_id`` slices (``build_resume_routing``).
    """
    streaming_service = VercelStreamingService()
    turn, stream_result = begin_turn(
        flow="resume",
        chat_id=chat_id,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request_id,
        auth_context=auth_context,
        log_prefix="stream_resume",
        error_log_prefix="stream_resume_chat",
    )

    from app.agents.chat.multi_agent_chat.shared.web_budget import start_turn_budget
    from app.services.token_tracking_service import start_turn

    accumulator = start_turn()
    # One hard web-search budget per reply, shared by every specialist.
    start_turn_budget()

    emit_stream_error = partial(
        emit_stream_terminal_error,
        streaming_service=streaming_service,
        flow="resume",
        request_id=request_id,
        thread_id=chat_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )

    session = async_session_maker()
    try:
        if user_id:
            await set_ai_responding(session, chat_id, UUID(user_id))

        requested_llm_config_id = llm_config_id

        # --- LLM config ---

        _t0 = time.perf_counter()
        try:
            from app.services.auto_model_pin_service import (
                resolve_or_get_pinned_llm_config_id,
            )

            pinned = await resolve_or_get_pinned_llm_config_id(
                session,
                thread_id=chat_id,
                workspace_id=workspace_id,
                user_id=user_id,
                selected_llm_config_id=llm_config_id,
            )
            llm_config_id = pinned.resolved_llm_config_id
            tracing.add_event(
                "model.pin.resolved",
                {
                    "pin.requested_id": requested_llm_config_id,
                    "pin.resolved_id": llm_config_id,
                    "pin.requires_image_input": False,
                },
            )
        except ValueError as pin_error:
            yield emit_stream_error(
                error_kind="server_error",
                error_code="SERVER_ERROR",
                diagnostic=str(pin_error),
            )
            yield streaming_service.format_done()
            return

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
            "[stream_resume] LLM config loaded in %.3fs", time.perf_counter() - _t0
        )

        if not llm:
            yield emit_stream_error(
                error_kind="server_error",
                error_code="SERVER_ERROR",
                diagnostic="Failed to create LLM instance",
            )
            yield streaming_service.format_done()
            return

        # --- Pre-stream setup ---

        _t0 = time.perf_counter()
        connector_service = await setup_connector_service(
            session, workspace_id=workspace_id
        )
        _perf_log.info(
            "[stream_resume] Connector service in %.3fs",
            time.perf_counter() - _t0,
        )

        _t0 = time.perf_counter()
        checkpointer = await get_chat_checkpointer()
        _perf_log.info(
            "[stream_resume] Checkpointer ready in %.3fs", time.perf_counter() - _t0
        )

        visibility = thread_visibility or ChatVisibility.PRIVATE
        turn.agent_mode = "multi"
        set_agent_mode(turn.span, turn.agent_mode)

        _t0 = time.perf_counter()
        agent_factory = create_multi_agent_chat_deep_agent
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
            auth_context=auth_context,
            retrieval_scope=retrieval_scope,
        )
        _perf_log.info(
            "[stream_resume] Agent created in %.3fs", time.perf_counter() - _t0
        )

        # Release the transaction before streaming (same rationale as stream_new_chat).
        await session.commit()
        session.expunge_all()

        _perf_log.info(
            "[stream_resume] Total pre-stream setup in %.3fs (chat_id=%s)",
            time.perf_counter() - turn.t_total,
            chat_id,
        )

        # --- Resume routing ---

        from langgraph.types import Command

        routing = await build_resume_routing(
            agent, chat_id=chat_id, decisions=decisions
        )
        paused_checkpoint = await checkpointer.aget_tuple(
            {"configurable": {"thread_id": str(chat_id)}}
        )
        paused_metadata = paused_checkpoint.metadata if paused_checkpoint else {}
        paused_turn_id = (
            paused_metadata.get("turn_id")
            if isinstance(paused_metadata, dict)
            else None
        )
        resumable_journal = await load_resumable_activity_journal(
            chat_id,
            turn_id=paused_turn_id if isinstance(paused_turn_id, str) else None,
        )

        config = {
            "configurable": {
                "thread_id": str(chat_id),
                "request_id": request_id or "unknown",
                "turn_id": stream_result.turn_id,
                # Per-``tool_call_id`` resume slices read by
                # ``SurfSenseCheckpointedSubAgentMiddleware``. Parallel
                # siblings each pop their own entry, so they never race.
                "surfsense_resume_value": routing.routed_resume_value,
            },
            # Same rationale as ``stream_new_chat``: effectively uncapped to
            # mirror the agent default and OpenCode's session loop. Doom-loop
            # / call-limit middleware enforce the real ceiling.
            "recursion_limit": 10_000,
        }

        # --- First SSE frames ---

        for sse in iter_initial_frames(
            streaming_service, turn_id=stream_result.turn_id
        ):
            yield sse

        # --- Assistant-shell persistence + id frame ---

        assistant_message_id = await persist_resume_assistant_shell(
            chat_id=chat_id,
            user_id=user_id,
            turn_id=stream_result.turn_id,
        )
        if assistant_message_id is None:
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
        if resumable_journal.timing is not None:
            stream_result.activity_timer = ActivityTimer.resume(
                resumable_journal.timing,
                now_ns=stream_result.activity_timer.active_since_ns,
            )
        yield emit_activity_timing_frame(
            streaming_service=streaming_service,
            content_builder=stream_result.content_builder,
            snapshot=stream_result.activity_timer.snapshot(),
        )

        runtime_context = build_resume_chat_runtime_context(
            workspace_id=workspace_id,
            request_id=request_id,
            turn_id=stream_result.turn_id,
        )

        # --- Stream loop ---

        _t_stream_start = time.perf_counter()
        runtime_rate_limit_recovered = False

        def _on_first_event() -> None:
            _perf_log.info(
                "[stream_resume] First agent event in %.3fs (stream), %.3fs (total) (chat_id=%s)",
                time.perf_counter() - _t_stream_start,
                time.perf_counter() - turn.t_total,
                chat_id,
            )

        async def _recover(exc: BaseException, first_event_seen: bool):
            nonlocal llm_config_id, llm, agent_config, runtime_rate_limit_recovered
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
                requires_image_input=False,
            )
            new_llm, new_agent_config, llm_load_err = await load_llm_bundle(
                session, config_id=llm_config_id, workspace_id=workspace_id
            )
            if llm_load_err:
                return None
            llm = new_llm
            agent_config = new_agent_config

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
                auth_context=auth_context,
                retrieval_scope=retrieval_scope,
            )
            _perf_log.info(
                "[stream_resume] Runtime rate-limit recovery repinned "
                "config_id=%s -> %s and rebuilt agent in %.3fs",
                previous_config_id,
                llm_config_id,
                time.perf_counter() - _t_rebuild,
            )
            log_rate_limit_recovered(
                flow="resume",
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
            input_data=Command(resume=routing.lg_resume_map),
            stream_result=stream_result,
            step_prefix=resume_step_prefix(stream_result.turn_id),
            initial_activities=resumable_journal.activities,
            resume_activity_id_by_tool_call=(
                resumable_journal.activity_id_by_tool_call
            ),
            resume_tool_call_ids=order_resume_tool_call_ids(
                resumable_journal, routing.pending_tool_call_ids
            ),
            fallback_commit_workspace_id=workspace_id,
            fallback_commit_created_by_id=user_id,
            fallback_commit_thread_id=chat_id,
            runtime_context=runtime_context,
            content_builder=stream_result.content_builder,
            recover=_recover,
            on_first_event=_on_first_event,
        ):
            yield sse

        _perf_log.info(
            "[stream_resume] Agent stream completed in %.3fs (chat_id=%s)",
            time.perf_counter() - _t_stream_start,
            chat_id,
        )

        # --- Finalize ---

        if stream_result.is_interrupted:
            tracing.add_event("chat.interrupted", {"chat.flow": "resume"})
            for sse in iter_token_usage_frame(
                streaming_service,
                accumulator=accumulator,
                log_label="interrupted resume_chat",
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

        for sse in iter_token_usage_frame(
            streaming_service, accumulator=accumulator, log_label="normal resume_chat"
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
            flow_label="resume",
        ):
            yield sse

    finally:
        await run_shielded_turn_cleanup(
            turn, session=session, stream_result=stream_result, accumulator=accumulator
        )

        # Release the lock from the original interrupted turn or any
        # re-interrupt/bailout.
        release_turn_lock(turn)

        agent = llm = connector_service = None
        stream_result = None
        session = None

        finish_turn(turn)
