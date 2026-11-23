"""Behavior tests for the turn bookkeeping shared by new-chat and resume-chat."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from app.agents.chat.runtime.errors import BusyError
from app.services.new_streaming_service import VercelStreamingService
from app.tasks.chat.streaming.flows.shared import turn as turn_mod
from app.tasks.chat.streaming.flows.shared.turn import (
    TurnContext,
    begin_turn,
    finish_turn,
    iter_terminal_exception_frames,
    release_turn_lock,
    run_shielded_turn_cleanup,
)
from app.tasks.chat.streaming.shared.stream_result import StreamResult

pytestmark = pytest.mark.unit


def _turn(**overrides: Any) -> TurnContext:
    fields: dict[str, Any] = {
        "flow": "new",
        "chat_id": 7,
        "workspace_id": 3,
        "user_id": "u-1",
        "request_id": "req-1",
        "auth_context": None,
        "log_prefix": "stream_x",
        "error_log_prefix": "stream_x_chat",
        "t_total": 0.0,
        "fs_mode": "cloud",
        "fs_platform": "web",
        "span_cm": "span-cm",
        "span": "span",
    }
    fields.update(overrides)
    return TurnContext(**fields)


# ---------------------------------------------------------------------------
# begin_turn
# ---------------------------------------------------------------------------


def test_begin_turn_fills_stream_result_and_opens_span(monkeypatch):
    calls: list[tuple[str, Any]] = []

    def fake_open(**kwargs):
        calls.append(("open", kwargs))
        return "cm", "span"

    monkeypatch.setattr(turn_mod, "open_chat_request_span", fake_open)
    monkeypatch.setattr(
        turn_mod,
        "log_file_contract",
        lambda label, result: calls.append(("contract", (label, result))),
    )

    turn, result = begin_turn(
        flow="regenerate",
        chat_id=12,
        workspace_id=4,
        user_id="u",
        request_id="r",
        auth_context=None,
        log_prefix="p",
        error_log_prefix="pe",
    )

    assert isinstance(result, StreamResult)
    assert result.request_id == "r"
    assert result.turn_id.startswith("12:")
    assert (result.filesystem_mode, result.client_platform) == ("cloud", "web")
    assert (turn.fs_mode, turn.fs_platform) == ("cloud", "web")
    assert (turn.span_cm, turn.span) == ("cm", "span")
    assert (turn.agent_mode, turn.outcome) == ("unknown", "success")
    assert (turn.error_category, turn.busy_error_raised) == (None, False)
    assert [name for name, _ in calls] == ["open", "contract"]
    opened = calls[0][1]
    assert opened["flow"] == "regenerate"
    assert opened["turn_id"] == result.turn_id
    assert opened["filesystem_mode"] == "cloud"
    assert opened["client_platform"] == "web"
    assert opened["agent_mode"] == "unknown"
    assert calls[1][1] == ("turn_start", result)


# ---------------------------------------------------------------------------
# iter_terminal_exception_frames
# ---------------------------------------------------------------------------


def test_terminal_frames_timing_first_then_state_then_error_frames(monkeypatch):
    seen: dict[str, Any] = {}
    turn = _turn(flow="resume", error_log_prefix="stream_resume_chat")
    stream_result = SimpleNamespace(content_builder=object(), activity_timer="timer")
    service = object()
    exc = ValueError("boom")

    monkeypatch.setattr(
        turn_mod,
        "emit_completed_activity_timing_frame_if_running",
        lambda **kw: "TIMING",
    )

    def fake_handle(exc_arg, **kwargs):
        seen["exc"] = exc_arg
        seen["kwargs"] = kwargs
        # state must not be touched before the timing frame has been consumed
        seen["state_at_call"] = (turn.outcome, turn.busy_error_raised)
        return iter(["F1", "F2"]), {
            "busy_error_raised": True,
            "chat_outcome": "SOME_CODE",
            "chat_error_category": "cat",
        }

    monkeypatch.setattr(turn_mod, "handle_terminal_exception", fake_handle)

    gen = iter_terminal_exception_frames(
        turn,
        exc,
        streaming_service=service,  # type: ignore[arg-type]
        stream_result=stream_result,  # type: ignore[arg-type]
        flow_label="resume",
    )
    assert next(gen) == "TIMING"
    assert "kwargs" not in seen, "classification waits until the timing frame is sent"
    assert list(gen) == ["F1", "F2"]

    assert seen["exc"] is exc
    assert seen["state_at_call"] == ("success", False)
    assert seen["kwargs"] == {
        "flow": "resume",
        "flow_label": "resume",
        "log_prefix": "stream_resume_chat",
        "streaming_service": service,
        "request_id": "req-1",
        "chat_id": 7,
        "workspace_id": 3,
        "user_id": "u-1",
        "chat_span": "span",
    }
    assert (turn.busy_error_raised, turn.outcome, turn.error_category) == (
        True,
        "SOME_CODE",
        "cat",
    )


def test_terminal_frames_skip_timing_when_no_builder_or_not_running(monkeypatch):
    turn = _turn()
    monkeypatch.setattr(
        turn_mod,
        "handle_terminal_exception",
        lambda exc, **kw: (
            iter(["F"]),
            {
                "busy_error_raised": False,
                "chat_outcome": "o",
                "chat_error_category": None,
            },
        ),
    )

    def boom(**kw):
        raise AssertionError("timing helper must not run without a builder")

    monkeypatch.setattr(
        turn_mod, "emit_completed_activity_timing_frame_if_running", boom
    )
    no_builder = SimpleNamespace(content_builder=None, activity_timer=None)
    frames = list(
        iter_terminal_exception_frames(
            turn,
            ValueError("x"),
            streaming_service=object(),  # type: ignore[arg-type]
            stream_result=no_builder,  # type: ignore[arg-type]
            flow_label="chat",
        )
    )
    assert frames == ["F"]
    assert turn.busy_error_raised is False

    monkeypatch.setattr(
        turn_mod,
        "emit_completed_activity_timing_frame_if_running",
        lambda **kw: None,
    )
    not_running = SimpleNamespace(content_builder=object(), activity_timer=None)
    assert list(
        iter_terminal_exception_frames(
            turn,
            ValueError("x"),
            streaming_service=object(),  # type: ignore[arg-type]
            stream_result=not_running,  # type: ignore[arg-type]
            flow_label="chat",
        )
    ) == ["F"]


def test_terminal_frames_real_path_prints_traceback_and_ends_with_done(capsys):
    turn = _turn(error_log_prefix="stream_new_chat")
    stream_result = SimpleNamespace(content_builder=None, activity_timer=None)
    frames: list[str] = []
    try:
        raise BusyError("locked")
    except Exception as exc:
        frames = list(
            iter_terminal_exception_frames(
                turn,
                exc,
                streaming_service=VercelStreamingService(),
                stream_result=stream_result,  # type: ignore[arg-type]
                flow_label="chat",
            )
        )

    out = capsys.readouterr().out
    assert "[stream_new_chat] Exception type: BusyError" in out
    assert "raise BusyError" in out, "traceback must be the live one"
    assert turn.busy_error_raised is True
    assert turn.outcome
    assert frames[-1].strip().endswith("[DONE]")


# ---------------------------------------------------------------------------
# run_shielded_turn_cleanup / release_turn_lock / finish_turn
# ---------------------------------------------------------------------------


@pytest.fixture
def order(monkeypatch) -> list[Any]:
    calls: list[Any] = []

    monkeypatch.setattr(
        turn_mod, "end_turn", lambda thread_id: calls.append(("end_turn", thread_id))
    )
    monkeypatch.setattr(
        turn_mod,
        "release_sandbox",
        lambda thread_id: calls.append(("release_sandbox", thread_id)),
    )

    async def fake_close(session, chat_id, *, clear_ai_responding=True):
        await anyio.sleep(0)
        calls.append(("close", session, chat_id, clear_ai_responding))

    async def fake_finalize(**kwargs):
        await anyio.sleep(0)
        calls.append(("finalize", kwargs))

    monkeypatch.setattr(turn_mod, "close_session_and_clear_ai_responding", fake_close)
    monkeypatch.setattr(turn_mod, "finalize_assistant_message", fake_finalize)
    monkeypatch.setattr(
        turn_mod,
        "capture_chat_turn_completed",
        lambda **kw: calls.append(("capture", kw)),
    )
    monkeypatch.setattr(turn_mod, "run_gc_pass", lambda **kw: calls.append(("gc", kw)))
    monkeypatch.setattr(
        turn_mod, "close_chat_request_span", lambda **kw: calls.append(("span", kw))
    )
    return calls


async def test_shielded_cleanup_runs_in_order(order):
    turn = _turn(agent_mode="multi", outcome="success")
    result = SimpleNamespace(turn_id="7:1")

    await run_shielded_turn_cleanup(
        turn, session="S", stream_result=result, accumulator="ACC"
    )

    assert [c[0] for c in order] == ["end_turn", "close", "finalize", "capture"]
    assert order[0] == ("end_turn", "7")
    assert order[1] == ("close", "S", 7, True)
    assert order[2][1] == {
        "stream_result": result,
        "chat_id": 7,
        "workspace_id": 3,
        "user_id": "u-1",
        "accumulator": "ACC",
        "log_prefix": "stream_x",
    }
    captured = order[3][1]
    assert captured["flow"] == "new"
    assert captured["outcome"] == "success"
    assert captured["agent_mode"] == "multi"
    assert captured["client_platform"] == "web"
    assert captured["filesystem_mode"] == "cloud"
    assert captured["turn_id"] == "7:1"
    assert captured["accumulator"] == "ACC"
    assert isinstance(captured["duration_ms"], int)


async def test_shielded_cleanup_busy_turn_skips_lock_and_flag(order):
    turn = _turn(busy_error_raised=True, outcome="BUSY", error_category="busy")

    await run_shielded_turn_cleanup(
        turn, session="S", stream_result=SimpleNamespace(), accumulator=None
    )

    assert [c[0] for c in order] == ["close", "finalize", "capture"]
    assert order[0] == ("close", "S", 7, False)
    assert order[2][1]["outcome"] == "BUSY"
    assert order[2][1]["error_category"] == "busy"
    assert order[2][1]["turn_id"] is None


async def test_shielded_cleanup_completes_under_a_cancelled_scope(order):
    """Client disconnect: the enclosing scope is cancelled, cleanup still runs."""
    turn = _turn()

    with anyio.CancelScope() as outer:
        outer.cancel()
        await run_shielded_turn_cleanup(
            turn, session="S", stream_result=SimpleNamespace(), accumulator=None
        )

    assert [c[0] for c in order] == ["end_turn", "close", "finalize", "capture"]


def test_release_turn_lock_ends_turn_unless_busy(order):
    """A finished turn frees its sandbox slot; a request turned away as busy
    must not, or a new thread could reclaim the running turn's sandbox."""
    release_turn_lock(_turn())
    assert order == [("release_sandbox", 7), ("end_turn", "7")]

    order.clear()
    release_turn_lock(_turn(busy_error_raised=True))
    assert order == []


def test_release_turn_lock_swallows_errors(monkeypatch):
    def boom(thread_id):
        raise RuntimeError("nope")

    monkeypatch.setattr(turn_mod, "end_turn", boom)
    release_turn_lock(_turn())


def test_finish_turn_collects_then_closes_span(order):
    turn = _turn(flow="resume", agent_mode="multi", outcome="X", error_category="cat")

    finish_turn(turn)

    assert [c[0] for c in order] == ["gc", "span"]
    assert order[0][1] == {"log_prefix": "stream_x", "chat_id": 7}
    span = order[1][1]
    assert span["span_cm"] == "span-cm"
    assert span["span"] == "span"
    assert span["chat_outcome"] == "X"
    assert span["chat_agent_mode"] == "multi"
    assert span["flow"] == "resume"
    assert span["chat_error_category"] == "cat"
    assert span["duration_seconds"] > 0
