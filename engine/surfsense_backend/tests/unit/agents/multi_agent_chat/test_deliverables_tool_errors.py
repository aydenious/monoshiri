"""Deliverables tools: ``ValueError`` goes back to the model, the rest ends the turn."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain.agents.middleware.tool_error import ToolErrorMiddleware
from langchain_core.messages import ToolMessage

from app.agents.chat.multi_agent_chat.subagents.builtins.deliverables import agent
from app.sandbox.protocol import SandboxUnavailableError


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        tool=SimpleNamespace(name="verify_artifact"),
        tool_call={"name": "verify_artifact", "id": "call-1", "args": {}},
    )


def test_value_error_becomes_content():
    assert agent._tool_error_content(ValueError("x"), _request()) == "Error: x"


@pytest.mark.parametrize(
    "exc", [SandboxUnavailableError("busy"), RuntimeError("boom"), KeyError("k")]
)
def test_other_errors_propagate(exc):
    assert agent._tool_error_content(exc, _request()) is None


@pytest.mark.asyncio
async def test_middleware_returns_value_error_as_tool_message():
    async def handler(_request):
        raise ValueError("needs a path")

    result = await ToolErrorMiddleware(agent._tool_error_content).awrap_tool_call(
        _request(), handler
    )
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.content == "Error: needs a path"
    assert result.tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_middleware_lets_sandbox_unavailable_end_the_turn():
    async def handler(_request):
        raise SandboxUnavailableError("busy")

    with pytest.raises(SandboxUnavailableError):
        await ToolErrorMiddleware(agent._tool_error_content).awrap_tool_call(
            _request(), handler
        )


def test_build_subagent_installs_tool_error_middleware(monkeypatch):
    captured = {}

    def fake_route_subagent(*_args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(agent, "build_route_subagent", fake_route_subagent)
    agent.build_subagent(dependencies={"workspace_id": 1})
    stack = captured["middleware_stack"]
    assert any(isinstance(mw, ToolErrorMiddleware) for mw in stack.values())
