"""read_sandbox_file returns refusals as text; raising would end the chat turn."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.chat.multi_agent_chat.subagents.builtins.deliverables.tools import (
    sandbox as sandbox_tools,
)
from app.sandbox import ExecResult

PATH = "/workspace/report.pdf"
NOT_TEXT = (
    f"Error: {PATH} is not UTF-8 text. read_sandbox_file reads text only; to keep "
    "a binary file (PDF, PPTX, DOCX, PNG), pass its path to save_artifact."
)


class FakeSession:
    def __init__(self, data: bytes, stat: ExecResult | None = None) -> None:
        self.data = data
        self.stat = stat or ExecResult(str(len(data)), 0)
        self.read_calls = 0

    async def run_command(self, command: str) -> ExecResult:
        return self.stat

    async def read_file(self, path: str) -> bytes:
        self.read_calls += 1
        return self.data


async def _read(monkeypatch, session: FakeSession) -> str:
    class Registry:
        async def get_session(self, thread_id, workspace_id):
            return session

    async def get_registry():
        return Registry()

    monkeypatch.setattr(sandbox_tools, "get_registry", get_registry)
    runtime = SimpleNamespace(
        tool_call_id="call-1",
        state={},
        config={"configurable": {"thread_id": "4::task:call-1"}},
    )
    tool = next(
        t
        for t in sandbox_tools.create_sandbox_tools(workspace_id=3)
        if t.name == "read_sandbox_file"
    )
    return await tool.coroutine(PATH, runtime)


@pytest.mark.asyncio
async def test_binary_file_returns_refusal(monkeypatch):
    assert await _read(monkeypatch, FakeSession(b"%PDF-1.7\x00\x01")) == NOT_TEXT


@pytest.mark.asyncio
async def test_invalid_utf8_returns_refusal(monkeypatch):
    assert await _read(monkeypatch, FakeSession(b"caf\xe9 \xff")) == NOT_TEXT


@pytest.mark.asyncio
async def test_oversize_returns_refusal_without_reading(monkeypatch):
    monkeypatch.setattr(sandbox_tools.app_config, "ARTIFACT_MAX_FILE_BYTES", 10)
    session = FakeSession(b"x", stat=ExecResult("11", 0))
    assert (
        await _read(monkeypatch, session)
        == "Error: sandbox file is 11 bytes; limit is 10 bytes"
    )
    assert session.read_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stat", [ExecResult("", 1), ExecResult("not-a-number", 0)])
async def test_stat_failure_returns_refusal(monkeypatch, stat):
    session = FakeSession(b"x", stat=stat)
    assert (
        await _read(monkeypatch, session)
        == f"Error: could not stat sandbox file: {PATH}"
    )
    assert session.read_calls == 0


@pytest.mark.asyncio
async def test_utf8_text_returns_content(monkeypatch):
    assert await _read(monkeypatch, FakeSession("héllo\n".encode())) == "héllo\n"
