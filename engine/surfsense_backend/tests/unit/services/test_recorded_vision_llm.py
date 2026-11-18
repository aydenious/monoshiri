"""Unit tests for ``RecordedVisionLLM``.

Validates that:

* Calling ``ainvoke`` routes through ``recorded_llm_call`` (one token-usage
  row per call) and forwards the inner LLM's response on success.
* The wrapper proxies non-overridden attributes to the inner LLM
  (``__getattr__``) so ``invoke`` / ``astream`` / ``with_structured_output``
  still work (they're not used in indexing today).
* A failing inner call still propagates: vision failure is an ordinary
  failure, the ETL pipeline falls back to the document parser.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


class _FakeInnerLLM:
    """Stand-in for ``langchain_litellm.ChatLiteLLM``."""

    def __init__(self, response: Any = "OCR'd content") -> None:
        self._response = response
        self.ainvoke_calls: list[Any] = []

    async def ainvoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        self.ainvoke_calls.append(input)
        return self._response

    def some_other_method(self, x: int) -> int:
        return x * 2


@contextlib.asynccontextmanager
async def _passthrough_recorder(**_kwargs):
    """Stand-in for recorded_llm_call that always allows the call to run."""

    class _Acc:
        total_cost_micros = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        grand_total = 0
        calls: list[Any] = []

        def per_message_summary(self) -> dict[str, dict[str, int]]:
            return {}

    yield _Acc()


@pytest.mark.asyncio
async def test_ainvoke_routes_through_the_recorder(monkeypatch):
    from app.services.recorded_vision_llm import RecordedVisionLLM

    captured_kwargs: list[dict[str, Any]] = []

    @contextlib.asynccontextmanager
    async def _spy_recorder(**kwargs):
        captured_kwargs.append(kwargs)
        async with _passthrough_recorder() as acc:
            yield acc

    monkeypatch.setattr(
        "app.services.recorded_vision_llm.recorded_llm_call",
        _spy_recorder,
        raising=False,
    )

    inner = _FakeInnerLLM(response="A red apple on a white table")
    user_id = uuid4()
    wrapper = RecordedVisionLLM(
        inner,
        user_id=user_id,
        workspace_id=99,
        base_model="openai/gpt-4o",
    )

    result = await wrapper.ainvoke([{"text": "what is this?"}])
    assert result == "A red apple on a white table"
    assert len(inner.ainvoke_calls) == 1
    assert len(captured_kwargs) == 1
    usage_kwargs = captured_kwargs[0]
    assert usage_kwargs["user_id"] == user_id
    assert usage_kwargs["workspace_id"] == 99
    assert usage_kwargs["usage_type"] == "vision_extraction"
    assert usage_kwargs["call_details"] == {"model": "openai/gpt-4o"}


@pytest.mark.asyncio
async def test_ainvoke_propagates_inner_failure(monkeypatch):
    from app.services.recorded_vision_llm import RecordedVisionLLM

    class _FailingInnerLLM:
        async def ainvoke(self, _input, *_args, **_kwargs):
            raise RuntimeError("provider 500")

    @contextlib.asynccontextmanager
    async def _recording(**_kwargs):
        yield object()

    monkeypatch.setattr(
        "app.services.recorded_vision_llm.recorded_llm_call",
        _recording,
        raising=False,
    )

    wrapper = RecordedVisionLLM(
        _FailingInnerLLM(),
        user_id=uuid4(),
        workspace_id=1,
        base_model="openai/gpt-4o",
    )

    with pytest.raises(RuntimeError, match="provider 500"):
        await wrapper.ainvoke([{"text": "x"}])


@pytest.mark.asyncio
async def test_proxies_non_overridden_attributes_to_inner():
    """``__getattr__`` forwards anything not on the proxy itself, so any
    method we didn't explicitly override (``invoke``, ``astream``,
    ``with_structured_output``, etc.) still works — just unrecorded, which is
    fine because the indexer only ever calls ainvoke.
    """
    from app.services.recorded_vision_llm import RecordedVisionLLM

    inner = _FakeInnerLLM()
    wrapper = RecordedVisionLLM(
        inner,
        user_id=uuid4(),
        workspace_id=1,
        base_model="openai/gpt-4o",
    )

    # ``some_other_method`` is on the inner only.
    assert wrapper.some_other_method(7) == 14


# ---------------------------------------------------------------------------
# get_vision_llm — which configs are wrapped
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class _FakeSession:
    def __init__(self, workspace):
        self._workspace = workspace

    async def execute(self, _stmt):
        return _FakeResult(self._workspace)


class _FakeChatLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("billing_tier", "wrapped"), [("free", False), ("premium", True)]
)
async def test_get_vision_llm_wraps_only_premium_global_configs(
    monkeypatch, billing_tier, wrapped
):
    """Only premium global configs record their own usage row.

    Free global configs (and BYOK) stay unwrapped, so a vision call made
    inside a chat turn keeps its tokens in that turn's accumulator instead of
    opening a separate ``scoped_turn``.
    """
    from types import SimpleNamespace
    from uuid import uuid4

    from app.agents.chat.runtime import llm_config
    from app.services import llm_service
    from app.services.recorded_vision_llm import RecordedVisionLLM

    workspace = SimpleNamespace(
        user_id=uuid4(), chat_model_id=None, vision_model_id=-7
    )
    monkeypatch.setattr(
        llm_service,
        "get_global_model",
        lambda _id: {
            "connection_id": -1,
            "model_id": "vision-model",
            "billing_tier": billing_tier,
        },
    )
    monkeypatch.setattr(llm_service, "get_global_connection", lambda _id: object())
    monkeypatch.setattr(llm_service, "_has_capability", lambda _m, _c: True)
    monkeypatch.setattr(
        llm_service,
        "_chat_litellm_from_resolved",
        lambda conn, model_id: ("openai/vision-model", {"model": model_id}),
    )
    monkeypatch.setattr(llm_config, "SanitizedChatLiteLLM", _FakeChatLLM)

    llm = await llm_service.get_vision_llm(_FakeSession(workspace), 42)

    assert isinstance(llm, RecordedVisionLLM) is wrapped
    if not wrapped:
        assert isinstance(llm, _FakeChatLLM)
