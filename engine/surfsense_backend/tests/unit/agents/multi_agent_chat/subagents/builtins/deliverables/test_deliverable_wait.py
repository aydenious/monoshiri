"""A failed poll (DB down at connect time) must not abort the wait."""

from __future__ import annotations

import enum
from types import SimpleNamespace

import asyncpg
import pytest

from app.agents.chat.multi_agent_chat.subagents.builtins.deliverables import (
    deliverable_wait as dw,
)


class _Status(enum.Enum):
    PENDING = "pending"
    READY = "ready"


class _Select:
    def where(self, _clause):
        return self


def _sessions(outcomes):
    """Yield one fake session per poll: an exception instance raises on connect."""

    class _Session:
        def __init__(self, outcome):
            self._outcome = outcome

        async def __aenter__(self):
            if isinstance(self._outcome, BaseException):
                raise self._outcome
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, _stmt):
            row = self._outcome
            return SimpleNamespace(first=lambda: row)

    it = iter(outcomes)
    return lambda: _Session(next(it))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outage",
    [
        ConnectionRefusedError("db down"),
        asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
    ],
)
async def test_connection_refused_poll_is_tolerated(monkeypatch, outage):
    monkeypatch.setattr(dw, "select", lambda *_cols: _Select())
    monkeypatch.setattr(
        dw,
        "shielded_async_session",
        _sessions(
            [
                outage,
                (_Status.PENDING,),
                (_Status.READY, "x"),
            ]
        ),
    )
    model = SimpleNamespace(id=None, __name__="Fake")

    status, row, _elapsed = await dw.wait_for_deliverable(
        model=model,
        row_id=1,
        columns=["status", "file"],
        terminal_statuses={_Status.READY},
        poll_interval_s=0,
    )

    assert status is _Status.READY
    assert row == (_Status.READY, "x")


@pytest.mark.asyncio
async def test_unexpected_poll_error_propagates(monkeypatch):
    monkeypatch.setattr(dw, "select", lambda *_cols: _Select())
    monkeypatch.setattr(dw, "shielded_async_session", _sessions([KeyError("bug")]))
    model = SimpleNamespace(id=None, __name__="Fake")

    with pytest.raises(KeyError):
        await dw.wait_for_deliverable(
            model=model,
            row_id=1,
            columns=["status"],
            terminal_statuses={_Status.READY},
            poll_interval_s=0,
        )
