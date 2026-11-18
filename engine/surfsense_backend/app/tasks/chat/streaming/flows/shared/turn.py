"""Turn bookkeeping shared by ``stream_new_chat`` and ``stream_resume_chat``.

Both orchestrators open a turn the same way (``StreamResult`` ids, filesystem
mode/platform, request span, ``turn_start`` contract log), turn an exception
into the same frame sequence, and tear down in the same order. This module
holds those sequences; the flow-specific phases stay in each orchestrator.

``TurnContext`` carries the per-turn values both sides need plus the four
mutable outcome fields the ``except`` branch writes and the ``finally`` branch
reads. ``stream_result``, ``session`` and ``accumulator`` are *not* stored on
it: the orchestrators drop their own references before the GC pass, and the
context must not keep them alive.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.multi_agent_chat.main_agent.middleware.busy_mutex import end_turn
from app.auth.context import AuthContext
from app.services.new_streaming_service import VercelStreamingService
from app.tasks.chat.streaming.contract.file_contract import log_file_contract
from app.tasks.chat.streaming.flows.shared.analytics import (
    capture_chat_turn_completed,
)
from app.tasks.chat.streaming.flows.shared.assistant_finalize import (
    finalize_assistant_message,
)
from app.tasks.chat.streaming.flows.shared.finally_cleanup import (
    close_session_and_clear_ai_responding,
    run_gc_pass,
)
from app.tasks.chat.streaming.flows.shared.span import (
    close_chat_request_span,
    open_chat_request_span,
)
from app.tasks.chat.streaming.flows.shared.terminal_error import (
    handle_terminal_exception,
)
from app.tasks.chat.streaming.relay.activity_sse import (
    emit_completed_activity_timing_frame_if_running,
)
from app.tasks.chat.streaming.shared.stream_result import StreamResult
from app.utils.perf import get_perf_logger

_perf_log = get_perf_logger()

Flow = Literal["new", "regenerate", "resume"]


@dataclass
class TurnContext:
    """Per-turn values shared by the start, error and cleanup sequences."""

    flow: Flow
    chat_id: int
    workspace_id: int
    user_id: str | None
    request_id: str | None
    auth_context: AuthContext | None
    # Prefix for the perf / gc / end_turn log lines (``[<log_prefix>] ...``).
    log_prefix: str
    # Prefix for the traceback ``print`` lines of the terminal-error branch.
    error_log_prefix: str
    t_total: float
    fs_mode: str
    fs_platform: str
    span_cm: Any
    span: Any
    agent_mode: str = "unknown"
    outcome: str = "success"
    error_category: str | None = None
    busy_error_raised: bool = False


def begin_turn(
    *,
    flow: Flow,
    chat_id: int,
    workspace_id: int,
    user_id: str | None,
    request_id: str | None,
    auth_context: AuthContext | None,
    log_prefix: str,
    error_log_prefix: str,
) -> tuple[TurnContext, StreamResult]:
    """Create the ``StreamResult``, open the request span, log ``turn_start``."""
    stream_result = StreamResult()
    t_total = time.perf_counter()
    fs_mode = "cloud"
    fs_platform = "web"
    stream_result.request_id = request_id
    stream_result.turn_id = f"{chat_id}:{int(time.time() * 1000)}"
    stream_result.filesystem_mode = fs_mode
    stream_result.client_platform = fs_platform

    agent_mode = "unknown"
    span_cm, span = open_chat_request_span(
        chat_id=chat_id,
        workspace_id=workspace_id,
        flow=flow,
        request_id=request_id,
        turn_id=stream_result.turn_id,
        filesystem_mode=fs_mode,
        client_platform=fs_platform,
        agent_mode=agent_mode,
    )
    log_file_contract("turn_start", stream_result)
    _perf_log.info(
        "[%s] filesystem_mode=%s client_platform=%s",
        log_prefix,
        fs_mode,
        fs_platform,
    )
    turn = TurnContext(
        flow=flow,
        chat_id=chat_id,
        workspace_id=workspace_id,
        user_id=user_id,
        request_id=request_id,
        auth_context=auth_context,
        log_prefix=log_prefix,
        error_log_prefix=error_log_prefix,
        t_total=t_total,
        fs_mode=fs_mode,
        fs_platform=fs_platform,
        span_cm=span_cm,
        span=span,
        agent_mode=agent_mode,
    )
    return turn, stream_result


def iter_terminal_exception_frames(
    turn: TurnContext,
    exc: Exception,
    *,
    streaming_service: VercelStreamingService,
    stream_result: StreamResult,
    flow_label: str,
) -> Iterator[str]:
    """Frames for the ``except Exception`` branch, in wire order.

    A still-running activity timing frame (if any) goes first, then the
    classification and span attributes are recorded and the status / error /
    finish frames follow. ``turn.busy_error_raised``, ``turn.outcome`` and
    ``turn.error_category`` are set after the timing frame is yielded and
    before the first error frame, exactly where the inline branch set them.

    Must be consumed from inside the ``except`` block: the traceback is
    printed with ``traceback.format_exc()``.
    """
    if stream_result.content_builder is not None:
        completed_timing_frame = emit_completed_activity_timing_frame_if_running(
            streaming_service=streaming_service,
            content_builder=stream_result.content_builder,
            timer=stream_result.activity_timer,
        )
        if completed_timing_frame is not None:
            yield completed_timing_frame
    frames, summary = handle_terminal_exception(
        exc,
        flow=turn.flow,
        flow_label=flow_label,
        log_prefix=turn.error_log_prefix,
        streaming_service=streaming_service,
        request_id=turn.request_id,
        chat_id=turn.chat_id,
        workspace_id=turn.workspace_id,
        user_id=turn.user_id,
        chat_span=turn.span,
    )
    if summary["busy_error_raised"]:
        turn.busy_error_raised = True
    turn.outcome = summary["chat_outcome"]
    turn.error_category = summary["chat_error_category"]
    yield from frames


async def run_shielded_turn_cleanup(
    turn: TurnContext,
    *,
    session: AsyncSession,
    stream_result: StreamResult,
    accumulator: Any,
) -> None:
    """The cancel-shielded first half of a flow's ``finally``.

    Shield the ENTIRE async cleanup from anyio cancel-scope cancellation.
    Starlette's BaseHTTPMiddleware uses anyio task groups; on client
    disconnect it cancels the scope with level-triggered cancellation, so
    every unshielded ``await`` would raise ``CancelledError`` immediately and
    ``session.close()`` would never run.

    ``end_turn`` is skipped when the turn was rejected as busy: this request
    never took the thread's lock, so releasing it here would unlock (and clear
    the cancel state of) the turn that is actually running.
    """
    with anyio.CancelScope(shield=True):
        if not turn.busy_error_raised:
            end_turn(str(turn.chat_id))

        await close_session_and_clear_ai_responding(
            session, turn.chat_id, clear_ai_responding=not turn.busy_error_raised
        )

        await finalize_assistant_message(
            stream_result=stream_result,
            chat_id=turn.chat_id,
            workspace_id=turn.workspace_id,
            user_id=turn.user_id,
            accumulator=accumulator,
            log_prefix=turn.log_prefix,
        )

        # Authoritative server-side product event. Inside the shield so it
        # survives client-disconnect cancellation (abandoned tabs still
        # produce a turn event), and while ``stream_result`` is still live
        # (the orchestrator drops it to None afterwards for GC).
        capture_chat_turn_completed(
            flow=turn.flow,
            outcome=turn.outcome,
            error_category=turn.error_category,
            workspace_id=turn.workspace_id,
            chat_id=turn.chat_id,
            user_id=turn.user_id,
            auth_context=turn.auth_context,
            agent_mode=turn.agent_mode,
            client_platform=turn.fs_platform,
            filesystem_mode=turn.fs_mode,
            turn_id=getattr(stream_result, "turn_id", None),
            request_id=turn.request_id,
            duration_ms=int((time.perf_counter() - turn.t_total) * 1000),
            accumulator=accumulator,
        )


def release_turn_lock(turn: TurnContext) -> None:
    """Release the busy lock for an interrupted / bailed-out turn.

    ``aafter_agent`` doesn't fire on ``interrupt()`` or early bailout. Skipped
    on ``BusyError`` (this request never held the lock).
    """
    if not turn.busy_error_raised:
        with contextlib.suppress(Exception):
            end_turn(str(turn.chat_id))
            _perf_log.info(
                "[%s] end_turn cleanup (chat_id=%s)", turn.log_prefix, turn.chat_id
            )


def finish_turn(turn: TurnContext) -> None:
    """GC pass, then record metrics and close the request span.

    The caller must drop its references to the agent graph, LLM and session
    first so the collector can reclaim them in this pass.
    """
    run_gc_pass(log_prefix=turn.log_prefix, chat_id=turn.chat_id)
    close_chat_request_span(
        span_cm=turn.span_cm,
        span=turn.span,
        chat_outcome=turn.outcome,
        chat_agent_mode=turn.agent_mode,
        flow=turn.flow,
        chat_error_category=turn.error_category,
        duration_seconds=time.perf_counter() - turn.t_total,
    )
