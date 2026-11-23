"""Registry behaviour that no provider can be trusted to enforce."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from app.sandbox.protocol import ExecResult, SandboxUnavailableError
from app.sandbox.registry import SandboxRegistry


class FakeSession:
    def __init__(self, thread_id: str) -> None:
        self.session_id = thread_id
        self.terminated = False

    async def execute(self, code: str, language: str = "python") -> ExecResult:
        return ExecResult(output="", exit_code=0)

    async def run_command(self, command: str) -> ExecResult:
        return ExecResult(output="", exit_code=0)

    async def read_file(self, path: str) -> bytes:
        return b""

    async def write_file(self, path: str, data: bytes) -> None:
        return None

    async def terminate(self) -> None:
        self.terminated = True


class FakeProvider:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.terminated: list[str] = []

    async def get_or_create_session(self, thread_id: str) -> FakeSession:
        self.created.append(thread_id)
        return FakeSession(thread_id)

    async def terminate_session(self, thread_id: str) -> None:
        self.terminated.append(thread_id)


async def test_disabled_deployment_refuses_before_building_a_provider(monkeypatch):
    import app.sandbox.registry as registry_module

    def unreachable() -> None:
        raise AssertionError("provider built with code execution disabled")

    monkeypatch.setattr(registry_module.app_config, "SANDBOX_ENABLED", False)
    monkeypatch.setattr("app.sandbox.factory.build_provider", unreachable)

    with pytest.raises(SandboxUnavailableError, match="disabled"):
        await registry_module.get_registry()


async def test_session_is_reused_within_a_thread():
    provider = FakeProvider()
    registry = SandboxRegistry(provider)

    first = await registry.get_session("t1", "w1")
    second = await registry.get_session("t1", "w1")

    assert first is second
    assert provider.created == ["t1"]


async def test_workspace_cap_rejects_rather_than_queues():
    provider = FakeProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=2)

    await registry.get_session("t1", "w1")
    await registry.get_session("t2", "w1")
    # A different workspace is unaffected by w1's usage.
    await registry.get_session("t3", "w2")

    with pytest.raises(SandboxUnavailableError):
        await registry.get_session("t4", "w1")


async def test_workspace_cap_is_atomic_across_concurrent_threads():
    class BlockingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def get_or_create_session(self, thread_id: str) -> FakeSession:
            self.started.set()
            await self.release.wait()
            return await super().get_or_create_session(thread_id)

    provider = BlockingProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=1)
    first = asyncio.create_task(registry.get_session("t1", "w1"))
    await provider.started.wait()
    second = asyncio.create_task(registry.get_session("t2", "w1"))
    provider.release.set()

    results = await asyncio.gather(first, second, return_exceptions=True)

    assert sum(isinstance(result, FakeSession) for result in results) == 1
    assert sum(isinstance(result, SandboxUnavailableError) for result in results) == 1


async def test_idle_sessions_are_reaped_and_killed():
    provider = FakeProvider()
    registry = SandboxRegistry(provider, idle_ttl_seconds=0)

    stale = await registry.get_session("t1", "w1")
    # Zero TTL means the next access sees t1 as idle; the cap would trip here
    # if reaping did not run first.
    await registry.get_session("t2", "w1")
    await registry.aclose()

    assert stale.terminated is True
    fresh = await registry.get_session("t1", "w1")
    assert fresh is not stale


async def test_evict_forgets_without_killing():
    provider = FakeProvider()
    registry = SandboxRegistry(provider)

    session = await registry.get_session("t1", "w1")
    await registry.evict("t1")

    assert session.terminated is False
    assert await registry.get_session("t1", "w1") is not session


async def test_terminate_is_safe_when_no_session_exists():
    provider = FakeProvider()
    registry = SandboxRegistry(provider)

    await registry.terminate("never-used")

    assert provider.terminated == ["never-used"]


async def test_full_workspace_reclaims_a_finished_threads_sandbox():
    provider = FakeProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=2)

    t1 = await registry.get_session("t1", "w1")
    await registry.get_session("t2", "w1")
    registry.release("t1")

    # t1's turn ended, so its slot is free for a new thread even though its
    # entry still counts against the cap until the idle TTL.
    await registry.get_session("t3", "w1")
    await registry.aclose()

    assert t1.terminated is True
    assert registry.get_cached("t1") is None
    assert registry.get_cached("t2") is not None


async def test_full_workspace_refuses_while_every_turn_is_running():
    provider = FakeProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=2)

    await registry.get_session("t1", "w1")
    await registry.get_session("t2", "w1")

    with pytest.raises(SandboxUnavailableError):
        await registry.get_session("t3", "w1")


async def test_reusing_a_released_sandbox_holds_its_slot_again():
    provider = FakeProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=2)

    t1 = await registry.get_session("t1", "w1")
    await registry.get_session("t2", "w1")
    registry.release("t1")

    assert await registry.get_session("t1", "w1") is t1

    with pytest.raises(SandboxUnavailableError):
        await registry.get_session("t3", "w1")


async def test_oldest_finished_sandbox_is_reclaimed_first(monkeypatch):
    import app.sandbox.registry as registry_module

    clock = itertools.count(1000)
    monkeypatch.setattr(registry_module.time, "monotonic", lambda: next(clock))

    provider = FakeProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=2)

    await registry.get_session("t1", "w1")
    t2 = await registry.get_session("t2", "w1")
    registry.release("t2")
    registry.release("t1")

    await registry.get_session("t3", "w1")
    await registry.aclose()

    assert t2.terminated is True
    assert registry.get_cached("t1") is not None


async def test_released_sandbox_in_another_workspace_is_not_reclaimed():
    provider = FakeProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=1)

    await registry.get_session("t1", "w2")
    registry.release("t1")
    await registry.get_session("t2", "w1")

    with pytest.raises(SandboxUnavailableError):
        await registry.get_session("t3", "w1")


async def test_release_sandbox_without_a_registry_is_a_no_op(monkeypatch):
    import app.sandbox.registry as registry_module

    monkeypatch.setattr(registry_module, "_registry", None)

    assert registry_module.release_sandbox("1") is None
    assert registry_module._registry is None


async def test_release_of_an_unknown_thread_is_a_no_op():
    provider = FakeProvider()
    registry = SandboxRegistry(provider)

    registry.release("never-used")

    assert registry.get_cached("never-used") is None
    assert provider.created == []


async def test_reclaimed_thread_comes_back_only_after_its_kill_lands():
    """A follow-up in a reclaimed thread must not re-adopt the dying sandbox."""
    gate = asyncio.Event()

    class SlowKill(FakeSession):
        async def terminate(self) -> None:
            await gate.wait()
            self.terminated = True

    class AdoptingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[tuple[str, bool | None]] = []
            self.latest: dict[str, SlowKill] = {}

        async def get_or_create_session(self, thread_id: str) -> SlowKill:
            old = self.latest.get(thread_id)
            self.seen.append((thread_id, old.terminated if old else None))
            self.latest[thread_id] = SlowKill(thread_id)
            return self.latest[thread_id]

    provider = AdoptingProvider()
    registry = SandboxRegistry(provider, max_sessions_per_workspace=1)

    await registry.get_session("t1", "w1")
    registry.release("t1")
    await registry.get_session("t2", "w1")  # reclaims t1; its kill is held
    registry.release("t2")
    comeback = asyncio.create_task(registry.get_session("t1", "w1"))
    for _ in range(10):
        await asyncio.sleep(0)
    assert not comeback.done()

    gate.set()
    await comeback
    await registry.aclose()

    assert provider.seen[-1] == ("t1", True)
