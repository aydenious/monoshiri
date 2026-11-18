"""Detach a chat turn from the HTTP request that started it.

The agent turn currently runs *inside* the SSE response generator
(``StreamingResponse(stream_new_chat(...))``). When the client disconnects,
Starlette cancels the response task and therefore that generator, so the
orchestrator's shielded ``finally`` runs mid-turn and persists a half-finished
assistant message (aborted tool calls, no text).

``run_detached`` moves the iteration into a background *pump* task. The
response consumes frames from a queue instead; when the client goes away only
the consumer stops. The pump keeps iterating the generator to its end, so the
orchestrator finishes the turn and saves the complete answer, which the app can
reload later.

Details that matter:

* The pump is started with ``asyncio.create_task`` from the consumer's task, so
  it inherits a *copy* of the current context. ContextVars the orchestrator sets
  inside the generator (the token accumulator, for one) keep working, and the
  generator is never driven from another loop or thread. anyio keeps its cancel
  scopes per task (a ``WeakKeyDictionary`` keyed by the task), so the new task
  is *not* inside the request's cancel scope: a disconnect cancels only the
  response task, never the pump.
* A strong reference to every pump lives in a module-level set until the task is
  done: ``asyncio`` only keeps a weak reference, so an un-referenced running
  task can be garbage collected mid-flight.
* The queue is bounded, but a full queue never stalls the pump (a stalled pump
  would stall the turn, the very thing this module exists to prevent). Frames
  are queued with ``put_nowait``; when the queue already holds
  ``max_queued_frames`` of them the consumer is treated as gone — exactly like
  a disconnect: the queue is drained, ``state.detached`` is set, one warning is
  logged and every later frame is dropped. The answer is persisted and
  reloadable, so losing the tail of it in a dead socket costs nothing, while
  the alternative (buffering a half-open socket for the whole deadline) is
  unbounded memory. The end marker is queued after the drain, so a consumer
  that wakes up later still ends instead of hanging.
* A deadline keeps a wedged turn bounded: ``on_timeout`` (a cooperative cancel
  in the routes) fires after ``max_seconds`` (30 min — attached turns had no
  limit before, this only stops runaways), and the pump is hard-cancelled
  ``grace_seconds`` later. The orchestrator's cleanup is shielded, so it still
  runs on that cancellation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Strong references to running pump tasks (see module docstring).
_pump_tasks: set[asyncio.Task[Any]] = set()

_SENTINEL = object()

# Upper bound on frames buffered for a consumer that stopped reading. One SSE
# frame is a few hundred bytes, so this is single-digit megabytes; anything
# larger means the reader is gone (half-open socket) and the turn should run
# detached instead of buffering the whole turn for a socket nobody reads.
_MAX_QUEUED_FRAMES = 20_000


class _PumpState:
    """Mutable state shared between the consumer and its pump task."""

    __slots__ = ("detached", "error")

    def __init__(self) -> None:
        self.detached = False
        self.error: BaseException | None = None


def _drain(queue: asyncio.Queue[object]) -> None:
    """Discard every buffered frame (sync on purpose)."""
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def run_detached(
    stream: AsyncIterator[str | bytes],
    *,
    label: str,
    on_timeout: Callable[[], object] | None = None,
    max_seconds: float = 1800,
    grace_seconds: float = 30,
    max_queued_frames: int = _MAX_QUEUED_FRAMES,
) -> AsyncIterator[str | bytes]:
    """Yield ``stream``'s frames, but let the stream outlive the consumer.

    Iterating this generator from ``StreamingResponse`` is equivalent to
    iterating ``stream`` directly while the client is connected. If the
    consumer is closed or cancelled, the underlying stream keeps running in the
    background to completion and its result is persisted by the orchestrator.

    ``max_queued_frames`` bounds the backlog held for a consumer that stops
    reading; past it the consumer is treated as gone (see the module
    docstring) and the generator simply ends once the stream does.
    """
    state = _PumpState()
    queue: asyncio.Queue[object] = asyncio.Queue()
    pump = asyncio.create_task(
        _pump(
            stream,
            queue=queue,
            state=state,
            label=label,
            on_timeout=on_timeout,
            max_seconds=max_seconds,
            grace_seconds=grace_seconds,
            max_queued_frames=max_queued_frames,
        ),
        name=f"detach-pump:{label}",
    )
    _pump_tasks.add(pump)
    # Without the strong reference above and this callback, a finished task
    # would linger in the set forever (and a running one could be GC'd).
    pump.add_done_callback(_pump_tasks.discard)

    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item
        if state.error is not None:
            # Same behaviour as iterating ``stream`` directly: an error while a
            # consumer is attached surfaces in the consumer.
            raise state.error
    except (GeneratorExit, asyncio.CancelledError):
        # The client went away. Stop consuming, but do NOT cancel the pump:
        # the turn must run to the end so the orchestrator persists a complete
        # answer.
        state.detached = True
        _drain(queue)
        logger.info("[detach] %s: client gone, turn continues", label)
        raise


async def _pump(
    stream: AsyncIterator[str | bytes],
    *,
    queue: asyncio.Queue[object],
    state: _PumpState,
    label: str,
    on_timeout: Callable[[], object] | None,
    max_seconds: float,
    grace_seconds: float,
    max_queued_frames: int,
) -> None:
    """Iterate ``stream`` to the end, queueing frames for the consumer."""
    deadline = asyncio.create_task(
        _enforce_deadline(
            asyncio.current_task(),
            label=label,
            on_timeout=on_timeout,
            max_seconds=max_seconds,
            grace_seconds=grace_seconds,
        ),
        name=f"detach-deadline:{label}",
    )
    try:
        async for frame in stream:
            if state.detached:
                continue  # drop: nobody is listening any more
            queued = queue.qsize()
            if queued >= max_queued_frames:
                # The reader is not keeping up (a suspended iPhone leaves its
                # socket half-open and stops reading). Buffer no more: treat it
                # as gone so the turn keeps running instead of stalling on a
                # full queue. The answer is persisted and reloadable.
                state.detached = True
                _drain(queue)
                logger.warning(
                    "[detach] %s: reader stalled, %d frames queued; "
                    "turn continues detached",
                    label,
                    queued,
                )
                continue
            queue.put_nowait(frame)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if state.detached:
            # Nobody can receive it: the turn failed after the client left.
            logger.exception("[detach] %s: turn failed after client left", label)
        state.error = exc
    finally:
        deadline.cancel()
        try:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    logger.exception(
                        "[detach] %s: closing the stream failed", label, exc_info=True
                    )
        finally:
            # Nested on purpose: the end marker must be queued even if
            # ``aclose()`` raises or is itself cancelled, or an attached (or
            # late-waking) consumer would wait for it forever. ``put_nowait``
            # makes this the only await-free step in the teardown.
            queue.put_nowait(_SENTINEL)


async def _enforce_deadline(
    pump: asyncio.Task[Any] | None,
    *,
    label: str,
    on_timeout: Callable[[], object] | None,
    max_seconds: float,
    grace_seconds: float,
) -> None:
    """Ask a wedged turn to stop, then cancel it after the grace period."""
    await asyncio.sleep(max_seconds)
    if pump is None or pump.done():
        return
    logger.warning(
        "[detach] %s: turn still running after %.0fs, requesting cancel",
        label,
        max_seconds,
    )
    if on_timeout is not None:
        try:
            on_timeout()
        except Exception:
            logger.exception("[detach] %s: on_timeout callback failed", label)
    await asyncio.sleep(grace_seconds)
    if pump.done():
        return
    logger.warning(
        "[detach] %s: still running %.0fs after the cancel request, cancelling it",
        label,
        grace_seconds,
    )
    pump.cancel()
