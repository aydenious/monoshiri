"""A turn rejected as busy must not clean up the turn that is actually running.

``BusyMutexMiddleware.abefore_agent`` raises ``BusyError`` when the thread's
lock is already held, i.e. before *this* request ever took it. The streaming
orchestrators' shielded ``finally`` then ran ``end_turn`` (release the lock,
bump the epoch, reset the cancel event) and cleared the ``ai_responding`` flag
for that thread anyway — cleaning up state owned by the concurrent turn. These
tests pin both halves of the fix: the flag stays the running turn's, and the
lock/cancel release is skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.tasks.chat.streaming.flows.shared import finally_cleanup

pytestmark = pytest.mark.unit

_FLOWS = Path(__file__).parents[3] / "app" / "tasks" / "chat" / "streaming" / "flows"
_ORCHESTRATORS = (
    _FLOWS / "new_chat" / "orchestrator.py",
    _FLOWS / "resume_chat" / "orchestrator.py",
)
_TURN = _FLOWS / "shared" / "turn.py"

_GUARDED_END_TURN = re.compile(
    r"if not turn\.busy_error_raised:\s*\n\s*end_turn\(str\(turn\.chat_id\)\)"
)
_GUARDED_CLEAR = "clear_ai_responding=not turn.busy_error_raised"


class _FakeSession:
    def __init__(self, *, rollback_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._rollback_error = rollback_error

    async def rollback(self) -> None:
        self.calls.append("rollback")
        if self._rollback_error is not None:
            raise self._rollback_error

    def expunge_all(self) -> None:
        self.calls.append("expunge_all")

    async def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def cleared(monkeypatch):
    """Record every ``clear_ai_responding`` call, on any session."""
    calls: list[tuple[object, int]] = []

    async def fake_clear(session, chat_id):
        calls.append((session, chat_id))

    monkeypatch.setattr(finally_cleanup, "_clear_ai_responding_state", fake_clear)
    return calls


async def test_busy_turn_closes_its_session_without_clearing_the_flag(cleared):
    session = _FakeSession()

    await finally_cleanup.close_session_and_clear_ai_responding(
        session, 7, clear_ai_responding=False
    )

    assert cleared == [], "a busy-rejected turn must not clear the running flag"
    assert session.calls == ["rollback", "expunge_all", "close"]


async def test_busy_turn_does_not_clear_the_flag_on_rollback_failure(
    cleared, monkeypatch
):
    from contextlib import asynccontextmanager

    opened: list[int] = []

    @asynccontextmanager
    async def fake_shielded_session():
        opened.append(1)  # what the real code opens to clear the flag
        yield _FakeSession()

    monkeypatch.setattr(
        finally_cleanup, "shielded_async_session", fake_shielded_session
    )
    session = _FakeSession(rollback_error=RuntimeError("rollback boom"))

    await finally_cleanup.close_session_and_clear_ai_responding(
        session, 7, clear_ai_responding=False
    )

    assert cleared == []
    assert opened == [], "no fallback session to clear the running turn's flag"
    assert session.calls == ["rollback", "expunge_all", "close"]


async def test_owning_turn_still_clears_the_flag(cleared):
    session = _FakeSession()

    await finally_cleanup.close_session_and_clear_ai_responding(session, 7)

    assert cleared == [(session, 7)]
    assert session.calls == ["rollback", "expunge_all", "close"]


async def test_owning_turn_still_clears_the_flag_on_rollback_failure(
    cleared, monkeypatch
):
    class _Fresh(_FakeSession):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_shielded_session():
        yield _Fresh()

    monkeypatch.setattr(
        finally_cleanup, "shielded_async_session", fake_shielded_session
    )
    session = _FakeSession(rollback_error=RuntimeError("rollback boom"))

    await finally_cleanup.close_session_and_clear_ai_responding(session, 7)

    assert [chat_id for _, chat_id in cleared] == [7]
    assert cleared[0][0] is not session, "the fallback session does the clear"
    assert session.calls == ["rollback", "expunge_all", "close"]


def test_shared_turn_cleanup_skips_lock_release_for_busy_turns():
    source = _TURN.read_text()

    assert len(_GUARDED_END_TURN.findall(source)) == 1, (
        "the shielded finally's end_turn must be gated on busy_error_raised"
    )
    assert source.count(_GUARDED_CLEAR) == 1, (
        "close_session_and_clear_ai_responding must receive the guard"
    )


@pytest.mark.parametrize("path", _ORCHESTRATORS, ids=lambda p: p.parent.name)
def test_orchestrator_finally_uses_the_guarded_cleanup(path: Path):
    source = path.read_text()

    assert source.count("await run_shielded_turn_cleanup(") == 1
    assert source.count("release_turn_lock(turn)") == 1
    assert "end_turn(" not in source, "lock release lives in the shared helper"


async def test_end_turn_would_release_another_turns_lock():
    """The hazard the guard closes: ``end_turn`` frees a lock nobody here took."""
    from app.agents.chat.multi_agent_chat.main_agent.middleware.busy_mutex.middleware import (
        end_turn,
        manager,
    )

    thread_id = "busy-cleanup-guard-hazard"
    lock = manager.lock_for(thread_id)
    await lock.acquire()  # the *running* turn holds it
    try:
        end_turn(thread_id)  # what a busy-rejected request's finally used to do
        assert not lock.locked(), "end_turn released the running turn's lock"
    finally:
        if lock.locked():
            lock.release()
