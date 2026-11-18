"""``build_route_subagent``: md files + tools feed ``pack_subagent`` with the right fallbacks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.chat.multi_agent_chat.subagents.shared import route_builder

pytestmark = pytest.mark.unit


@pytest.fixture
def packed(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        route_builder, "pack_subagent", lambda **kw: captured.update(kw) or kw
    )
    return captured


def _index():
    return SimpleNamespace(
        NAME="demo",
        RULESET="rules",
        load_tools=lambda *, dependencies: [f"tool-{dependencies['k']}"],
    )


def test_uses_md_files_and_appends_mcp_tools(monkeypatch, packed):
    files = {"description": "  A route.  ", "system_prompt": " Prompt. "}
    monkeypatch.setattr(route_builder, "read_md_file", lambda pkg, stem: files[stem])

    route_builder.build_route_subagent(
        "pkg", _index(), "fallback", dependencies={"k": 1}, mcp_tools=["mcp"]
    )

    assert packed["name"] == "demo"
    assert packed["description"] == "A route."
    assert packed["system_prompt"] == "Prompt."
    assert packed["tools"] == ["tool-1", "mcp"]
    assert packed["ruleset"] == "rules"


def test_falls_back_to_description_and_adds_prompt_suffix(monkeypatch, packed):
    files = {"description": "", "system_prompt": "Prompt."}
    monkeypatch.setattr(route_builder, "read_md_file", lambda pkg, stem: files[stem])

    route_builder.build_route_subagent(
        "pkg",
        _index(),
        "fallback",
        dependencies={"k": 2},
        system_prompt_suffix="Extra.",
    )

    assert packed["description"] == "fallback"
    assert packed["system_prompt"] == "Prompt.\n\nExtra."
    assert packed["tools"] == ["tool-2"]
