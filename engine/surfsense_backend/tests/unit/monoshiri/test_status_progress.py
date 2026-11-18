"""Conversion progress fields on DocumentStatus and computed REST health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.conversion.status import STALLED_AFTER
from app.db import DocumentStatus
from app.schemas.documents import DocumentStatusSchema

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _doc(status, updated_at=None):
    return SimpleNamespace(status=status, updated_at=updated_at)


def test_processing_carries_progress_and_updated_at():
    before = datetime.now(UTC)
    status = DocumentStatus.processing(
        stage="converting", pages_done=20, pages_total=400
    )

    assert status["state"] == "processing"
    assert status["stage"] == "converting"
    assert status["pages_done"] == 20
    assert status["pages_total"] == 400
    assert status["updated_at"].endswith("Z")
    parsed = _parse(status["updated_at"])
    assert abs((parsed - before).total_seconds()) < 5


def test_processing_omits_none_fields_but_accepts_reason():
    status = DocumentStatus.processing(reason="retrying")
    assert status["reason"] == "retrying"
    assert "stage" not in status
    assert "pages_done" not in status
    assert "pages_total" not in status


def test_processing_without_args_is_backward_compatible():
    status = DocumentStatus.processing()
    assert status["state"] == "processing"
    assert set(status) == {"state", "updated_at"}


def test_pending_is_backward_compatible_and_stamped():
    status = DocumentStatus.pending()
    assert status["state"] == "pending"
    assert set(status) == {"state", "updated_at"}
    assert _parse(status["updated_at"]).tzinfo is not None


def test_stage_constants_are_the_contract_values():
    assert DocumentStatus.STAGE_QUEUED == "queued"
    assert DocumentStatus.STAGE_CONVERTING == "converting"
    assert DocumentStatus.STAGE_WAITING == "waiting_for_converter"
    assert DocumentStatus.STAGE_INDEXING == "indexing"


@pytest.mark.parametrize(
    ("status", "document_updated_at", "expected_health", "expected_state"),
    [
        (None, None, None, None),
        ({"state": "ready"}, None, None, "ready"),
        (
            {"state": "failed", "reason": "boom"},
            None,
            None,
            "failed",
        ),
        (
            {
                "state": "processing",
                "stage": "waiting_for_converter",
                "updated_at": (NOW - timedelta(hours=1)).isoformat(),
            },
            None,
            "stalled",
            "processing",
        ),
        (
            # A live outage wait refreshes updated_at on every retry (the max
            # backoff is 10 min), so it still reads as waiting.
            {
                "state": "processing",
                "stage": "waiting_for_converter",
                "updated_at": (NOW - timedelta(minutes=20)).isoformat(),
            },
            None,
            "waiting",
            "processing",
        ),
        (
            {
                "state": "processing",
                "updated_at": (NOW - timedelta(minutes=1)).isoformat(),
            },
            None,
            "working",
            "processing",
        ),
        (
            {
                "state": "processing",
                "updated_at": (NOW - timedelta(minutes=14)).isoformat(),
            },
            None,
            "working",
            "processing",
        ),
        (
            {
                "state": "processing",
                "updated_at": (NOW - timedelta(minutes=20)).isoformat(),
            },
            None,
            "stalled",
            "processing",
        ),
        # Legacy row: no status.updated_at, falls back to documents.updated_at.
        ({"state": "processing"}, NOW - timedelta(minutes=20), "stalled", "processing"),
    ],
)
def test_health_rules(status, document_updated_at, expected_health, expected_state):
    schema = DocumentStatusSchema.from_document(_doc(status, document_updated_at), NOW)
    if status is None:
        assert schema is None
        return
    assert schema is not None
    assert schema.state == expected_state
    assert schema.health == expected_health


def test_health_rule_stalled_after_is_fifteen_minutes():
    assert timedelta(minutes=15) == STALLED_AFTER


def test_from_document_carries_progress_fields_and_reason():
    status = DocumentStatus.processing(
        stage="converting", pages_done=20, pages_total=400
    )
    schema = DocumentStatusSchema.from_document(_doc(status), NOW)
    assert schema is not None
    assert schema.stage == "converting"
    assert schema.pages_done == 20
    assert schema.pages_total == 400
    assert schema.updated_at is not None
    assert schema.health == "working"


def test_from_document_tolerates_naive_datetimes():
    schema = DocumentStatusSchema.from_document(
        _doc({"state": "processing"}, datetime(2026, 11, 6, 11, 0)), NOW
    )
    assert schema is not None
    assert schema.health == "stalled"


async def test_keepalive_bug_never_aborts_the_guarded_work(caplog):
    """An unexpected heartbeat error is logged with its traceback, not raised."""
    import asyncio

    from app.conversion.status import status_keepalive

    def broken_maker():
        raise AttributeError("'NoneType' object has no attribute 'send'")

    async with status_keepalive(1, every=0.001, session_maker=broken_maker):
        await asyncio.sleep(0.02)

    assert any(r.exc_info and "unexpectedly" in r.getMessage() for r in caplog.records)


async def test_keepalive_db_outage_is_a_warning(caplog):
    """A raw asyncpg error at connect time only costs the heartbeat, at WARNING."""
    import asyncio
    import logging

    import asyncpg

    from app.conversion.status import status_keepalive

    def starting_up():
        raise asyncpg.exceptions.CannotConnectNowError(
            "the database system is starting up"
        )

    with caplog.at_level(logging.WARNING):
        async with status_keepalive(1, every=0.001, session_maker=starting_up):
            await asyncio.sleep(0.02)

    keepalive = [r for r in caplog.records if "keepalive" in r.getMessage()]
    assert keepalive and all(r.levelno == logging.WARNING for r in keepalive)
