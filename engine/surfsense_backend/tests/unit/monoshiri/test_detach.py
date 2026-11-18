"""Unit tests for ``app.tasks.chat.streaming.detach`` (turn outlives the client)."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import get_type_hints

import pytest
from starlette.responses import StreamingResponse

from app.tasks.chat.streaming import detach

pytestmark = pytest.mark.unit

ROUTES = Path(__file__).parents[3] / "app" / "routes" / "new_chat_routes.py"
_DETACHED_CALL = re.compile(
    r"run_detached\(\s*(stream_new_chat|stream_resume_chat|stream_with_cleanup)\("
)


async def _wait_for(predicate, timeout: float = 1.0) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` elapses."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def _counting_stream(frames: int, record: list[str]):
    """Async generator of ``frames`` frames recording its end and its finally."""

    async def gen():
        try:
            for i in range(frames):
                await asyncio.sleep(0)
                yield f"frame-{i}"
            record.append("done")
        finally:
            record.append("finally")

    return gen()


async def test_frames_in_order():
    record: list[str] = []
    got = [
        frame
        async for frame in detach.run_detached(_counting_stream(3, record), label="t")
    ]
    assert got == ["frame-0", "frame-1", "frame-2"]
    assert record == ["done", "finally"]


async def test_consumer_closes_early_producer_finishes():
    record: list[str] = []
    consumer = detach.run_detached(_counting_stream(20, record), label="t")

    assert await consumer.__anext__() == "frame-0"
    assert await consumer.__anext__() == "frame-1"
    await consumer.aclose()

    assert await _wait_for(lambda: record == ["done", "finally"])
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_consumer_cancelled_producer_finishes():
    record: list[str] = []
    release = asyncio.Event()
    first = asyncio.Event()

    async def gen():
        try:
            yield "frame-0"
            await release.wait()
            yield "frame-1"
            record.append("done")
        finally:
            record.append("finally")

    async def consume():
        async for _ in detach.run_detached(gen(), label="t"):
            first.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    release.set()
    assert await _wait_for(lambda: record == ["done", "finally"])
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_error_reaches_attached_consumer():
    async def gen():
        yield "frame-0"
        raise ValueError("boom")

    consumer = detach.run_detached(gen(), label="t")
    assert await consumer.__anext__() == "frame-0"
    with pytest.raises(ValueError, match="boom"):
        await consumer.__anext__()

    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_error_after_detach_is_logged_not_raised(caplog):
    record: list[str] = []
    release = asyncio.Event()

    async def gen():
        try:
            yield "frame-0"
            await release.wait()
            raise ValueError("late boom")
        finally:
            record.append("finally")

    with caplog.at_level(logging.ERROR, logger=detach.__name__):
        consumer = detach.run_detached(gen(), label="t")
        assert await consumer.__anext__() == "frame-0"
        await consumer.aclose()
        release.set()

        assert await _wait_for(lambda: record == ["finally"])
        assert await _wait_for(lambda: not detach._pump_tasks)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a late failure must be logged"


async def test_deadline_calls_on_timeout_then_cancels():
    record: list[str] = []
    calls: list[int] = []

    async def gen():
        try:
            await asyncio.sleep(3600)
            yield "never"
        finally:
            record.append("finally")

    frames = [
        frame
        async for frame in detach.run_detached(
            gen(),
            label="t",
            on_timeout=lambda: calls.append(1),
            max_seconds=0.05,
            grace_seconds=0.05,
        )
    ]

    assert frames == []
    assert calls == [1]
    assert await _wait_for(lambda: record == ["finally"])
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_cooperative_timeout_no_hard_cancel():
    record: list[str] = []
    cancelled: list[int] = []
    stop = asyncio.Event()

    async def gen():
        try:
            while not stop.is_set():
                await asyncio.sleep(0.01)
                yield "tick"
            record.append("done")
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
        finally:
            record.append("finally")

    frames = [
        frame
        async for frame in detach.run_detached(
            gen(),
            label="t",
            on_timeout=stop.set,
            max_seconds=0.05,
            grace_seconds=5,
        )
    ]

    assert frames, "frames flowed until the cooperative stop"
    assert record == ["done", "finally"]
    assert cancelled == []
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_detached_pump_drops_frames():
    """After a detach the pump keeps draining the stream without buffering."""
    record: list[str] = []
    release = asyncio.Event()

    async def gen():
        try:
            yield "frame-0"
            await release.wait()
            for i in range(5000):
                yield f"frame-{i}"
            record.append("done")
        finally:
            record.append("finally")

    consumer = detach.run_detached(gen(), label="t")
    assert await consumer.__anext__() == "frame-0"
    await consumer.aclose()

    release.set()
    assert await _wait_for(lambda: record == ["done", "finally"], timeout=5.0)
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_no_leaked_tasks():
    record: list[str] = []
    got = [
        frame
        async for frame in detach.run_detached(_counting_stream(3, record), label="t")
    ]
    assert got == ["frame-0", "frame-1", "frame-2"]
    assert await _wait_for(lambda: not detach._pump_tasks)


def test_every_streaming_route_is_detached():
    """Guard: all three SSE routes must drive their stream through ``run_detached``."""
    source = ROUTES.read_text()
    assert sorted(_DETACHED_CALL.findall(source)) == [
        "stream_new_chat",
        "stream_resume_chat",
        "stream_with_cleanup",
    ]
    # A wedged turn stays bounded only because each route passes the
    # cooperative cancel as its deadline hook.
    assert source.count("on_timeout=lambda: request_cancel(") == 3


async def test_stalled_consumer_never_stalls_the_turn():
    # A suspended iPhone can leave its socket half-open and stop reading. The
    # turn must still run to its end, and the frames must all arrive in order
    # once the reader comes back (no lost end marker on a long backlog). The
    # backlog of exactly ``_MAX_QUEUED_FRAMES`` frames still fits the default
    # cap, so nothing is dropped.
    record: list[str] = []
    frames = detach._MAX_QUEUED_FRAMES
    consumer = detach.run_detached(_counting_stream(frames, record), label="t")

    assert await consumer.__anext__() == "frame-0"
    assert await _wait_for(lambda: record == ["done", "finally"])

    rest = [frame async for frame in consumer]
    assert rest == [f"frame-{i}" for i in range(1, frames)]
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_queue_cap_detaches_a_stalled_reader(caplog):
    # A reader that stops draining must not be able to grow the queue without
    # bound: past the cap the turn is treated as detached (like a disconnect)
    # and keeps running, rather than stalling on a full queue.
    cap = 10
    record: list[str] = []
    with caplog.at_level(logging.WARNING, logger=detach.__name__):
        consumer = detach.run_detached(
            _counting_stream(100, record), label="t", max_queued_frames=cap
        )
        assert await consumer.__anext__() == "frame-0"
        # Read no further: the queue fills up to the cap while the turn runs.
        assert await _wait_for(lambda: record == ["done", "finally"])

        # The backlog was drained, but the end marker still arrives, so a
        # late-waking reader ends instead of hanging.
        rest = [frame async for frame in consumer]

    assert len(rest) <= cap + 1, "the buffer never exceeds the cap"
    assert rest == []
    warnings = [
        entry
        for entry in caplog.records
        if entry.name == detach.__name__ and entry.levelno == logging.WARNING
    ]
    assert len(warnings) == 1, "one warning per stalled reader, not one per frame"
    assert "reader stalled, 10 frames queued; turn continues detached" in (
        warnings[0].getMessage()
    )
    assert await _wait_for(lambda: not detach._pump_tasks)


@pytest.mark.parametrize(
    "error", [RuntimeError("aclose boom"), asyncio.CancelledError()]
)
async def test_sentinel_reaches_consumer_even_if_aclose_fails(error):
    # Closing the stream happens in the pump's finally. If that fails (or is
    # cancelled) the end marker must still be queued, or an attached consumer
    # would wait for it forever.
    class _BadClose:
        def __init__(self, agen):
            self._agen = agen

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self._agen.__anext__()

        async def aclose(self):
            raise error

    async def gen():
        yield "frame-0"

    async def consume() -> list[str]:
        return [
            frame async for frame in detach.run_detached(_BadClose(gen()), label="t")
        ]

    frames = await asyncio.wait_for(consume(), timeout=1.0)

    assert frames == ["frame-0"]
    assert await _wait_for(lambda: not detach._pump_tasks)


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("stream", AsyncIterator[str | bytes]),
        ("return", AsyncIterator[str | bytes]),
    ],
)
def test_run_detached_accepts_and_yields_bytes(attr, expected):
    # ``stream_with_cleanup`` yields raw bytes (file downloads); the annotation
    # must say so instead of claiming ``str`` only.
    hints = get_type_hints(detach.run_detached)
    assert hints[attr] == expected


def test_default_deadline_is_thirty_minutes():
    # Attached turns had no limit at all before the detach; the deadline only
    # stops runaway turns, so it must be generous (30 min, not 15).
    default = inspect.signature(detach.run_detached).parameters["max_seconds"].default
    assert default == 1800


async def test_bytes_frames_pass_through():
    async def gen():
        yield b"a"
        yield b"b"

    frames = [frame async for frame in detach.run_detached(gen(), label="t")]

    assert frames == [b"a", b"b"]


def _http_scope() -> dict:
    """Minimal ASGI HTTP scope, as uvicorn builds it (spec_version 2.3)."""
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/new_chat",
        "raw_path": b"/api/v1/new_chat",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 4242),
        "server": ("testserver", 80),
    }


def _asgi_stream(record: list[str], frames: int = 20):
    """A fake SSE turn; ``record`` gets ``last`` at the end, then ``finally``."""

    async def gen():
        try:
            for i in range(frames):
                await asyncio.sleep(0.01)
                yield f"data: frame-{i}\n\n"
            record.append("last")
        finally:
            record.append("finally")

    return gen()


async def test_asgi_disconnect_leaves_the_turn_running():
    # The central claim, exercised through real Starlette: a client disconnect
    # cancels the response task inside Starlette's anyio task group, but not
    # the pump, so the generator still runs to its end.
    record: list[str] = []
    first_body = asyncio.Event()
    sent: list[dict] = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message["body"]:
            first_body.set()

    async def receive():
        await first_body.wait()
        return {"type": "http.disconnect"}

    response = StreamingResponse(detach.run_detached(_asgi_stream(record), label="t"))
    await asyncio.wait_for(response(_http_scope(), receive, send), timeout=5.0)

    assert any(message["type"] == "http.response.body" for message in sent)
    assert await _wait_for(lambda: record == ["last", "finally"], timeout=2.0)
    assert await _wait_for(lambda: not detach._pump_tasks)


async def test_asgi_half_open_socket_leaves_the_turn_running():
    # A write to a half-open socket raises OSError in ``send`` (Starlette's
    # spec_version 2.3 path propagates it). The turn must still finish.
    record: list[str] = []
    sent: list[dict] = []
    body_chunks = 0

    async def send(message):
        nonlocal body_chunks
        if message["type"] == "http.response.body":
            body_chunks += 1
            if body_chunks == 3:
                raise OSError("broken pipe")
        sent.append(message)

    async def receive():
        await asyncio.Event().wait()  # never disconnects: the write fails first
        return {"type": "http.disconnect"}

    response = StreamingResponse(detach.run_detached(_asgi_stream(record), label="t"))
    with pytest.raises(OSError):
        await asyncio.wait_for(response(_http_scope(), receive, send), timeout=5.0)

    body = [m for m in sent if m["type"] == "http.response.body"]
    assert len(body) == 2, "the write failed on the third chunk"
    assert await _wait_for(lambda: record == ["last", "finally"], timeout=2.0)
    assert await _wait_for(lambda: not detach._pump_tasks)
    # Nobody will iterate the response any more; close it so the generator
    # does not outlive the test.
    await response.body_iterator.aclose()
