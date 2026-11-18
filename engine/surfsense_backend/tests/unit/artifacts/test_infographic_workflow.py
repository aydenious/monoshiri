from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from app.agents.chat.multi_agent_chat.subagents.builtins.deliverables.tools import (
    sandbox as sandbox_tools,
)
from app.artifacts.infographic.generation import GeneratedInfographic
from app.artifacts.infographic.presets import QUESTION_PRESET_ID, resolve_visual_style
from app.artifacts.infographic.selection import (
    issue_selection_token,
    read_generation_state,
)
from tests.utils.fake_sandbox import FakeSandboxSession


@asynccontextmanager
async def _db_session():
    yield object()


async def test_generation_writes_png_markdown_and_enforces_one_repair(
    monkeypatch,
) -> None:
    session = FakeSandboxSession()
    resolved = resolve_visual_style("sketch-note", "")
    token = issue_selection_token(
        workspace_id=7,
        thread_id=11,
        preset_id=QUESTION_PRESET_ID,
        preset_version=1,
        resolved=resolved,
        secret_key="secret",
    )
    generated = GeneratedInfographic(
        png=b"\x89PNG\r\n\x1a\nimage",
        width=1200,
        height=800,
        image_gen_model_id=17,
        provider_model="provider/model",
    )
    calls: list[tuple[str, ...]] = []

    async def fake_generate(_session, **kwargs):
        calls.append(tuple(kwargs["repair_findings"]))
        return generated

    monkeypatch.setattr(sandbox_tools.app_config, "SECRET_KEY", "secret")
    monkeypatch.setattr(sandbox_tools, "shielded_async_session", _db_session)
    monkeypatch.setattr(sandbox_tools, "generate_infographic", fake_generate)

    first = json.loads(
        await sandbox_tools._generate_infographic_file(
            session=session,
            workspace_id=7,
            thread_id=11,
            factual_markdown="# Guide\n\n## One\nFirst.",
            output_path="/workspace/guide.png",
            selection_token=token,
            output_constraints="4:3",
            repair_findings=None,
        )
    )
    assert first["attempt"] == 1
    assert session.files["/workspace/guide.png"] == generated.png
    assert session.files["/workspace/guide.md"] == b"# Guide\n\n## One\nFirst."

    second = json.loads(
        await sandbox_tools._generate_infographic_file(
            session=session,
            workspace_id=7,
            thread_id=11,
            factual_markdown="# Guide\n\n## One\nFirst.",
            output_path="/workspace/guide.png",
            selection_token=token,
            output_constraints="4:3",
            repair_findings=["The heading is clipped."],
        )
    )
    assert second["attempt"] == 2
    assert calls == [(), ("The heading is clipped.",)]

    with pytest.raises(ValueError, match="only one repair"):
        await sandbox_tools._generate_infographic_file(
            session=session,
            workspace_id=7,
            thread_id=11,
            factual_markdown="# Guide\n\n## One\nFirst.",
            output_path="/workspace/guide.png",
            selection_token=token,
            output_constraints="4:3",
            repair_findings=["Still clipped."],
        )

    state = await read_generation_state(
        session,
        "/workspace/guide.png",
        workspace_id=7,
        secret_key="secret",
    )
    assert state is not None
    assert state.attempts == 2
    assert state.manifest_provenance()["resolved_style_id"] == "sketch-note"


async def test_generation_rejects_untrusted_or_outside_paths(monkeypatch) -> None:
    monkeypatch.setattr(sandbox_tools.app_config, "SECRET_KEY", "secret")
    session = FakeSandboxSession()

    with pytest.raises(ValueError, match="under /workspace"):
        await sandbox_tools._generate_infographic_file(
            session=session,
            workspace_id=7,
            thread_id=11,
            factual_markdown="# Guide\n\n## One\nFirst.",
            output_path="/tmp/guide.png",
            selection_token="invalid",
            output_constraints=None,
            repair_findings=None,
        )


def test_selection_token_garbage_is_invalid_but_unexpected_errors_propagate(
    monkeypatch,
) -> None:
    import pytest

    from app.artifacts.infographic import selection
    from app.artifacts.infographic.selection import read_selection_token

    kwargs = {"workspace_id": 7, "thread_id": 11, "secret_key": "secret"}
    for bad in ("no-dot", "a.b", "é.é"):
        with pytest.raises(ValueError, match="token is invalid"):
            read_selection_token(bad, **kwargs)

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(selection, "_sign", boom)
    with pytest.raises(RuntimeError):
        read_selection_token("abcd.efgh", **kwargs)


async def test_generation_state_garbage_is_invalid_but_unexpected_errors_propagate(
    monkeypatch,
) -> None:
    import pytest

    from app.artifacts.infographic import selection
    from app.artifacts.infographic.selection import read_generation_state

    class _Session:
        session_id = "s"

        def __init__(self, raw):
            self.raw = raw

        async def read_file(self, _path):
            return self.raw

    for raw in (b"not json", b"[]", b'{"payload": {}}'):
        with pytest.raises(ValueError, match="metadata is invalid"):
            await read_generation_state(
                _Session(raw), "/out.png", workspace_id=7, secret_key="secret"
            )

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(selection, "_sign", boom)
    with pytest.raises(RuntimeError):
        await read_generation_state(
            _Session(b'{"payload": {}, "signature": "x"}'),
            "/out.png",
            workspace_id=7,
            secret_key="secret",
        )


async def test_deeply_nested_json_is_invalid_not_a_crash() -> None:
    import base64

    import pytest

    from app.artifacts.infographic import selection
    from app.artifacts.infographic.selection import (
        read_generation_state,
        read_selection_token,
    )

    class _Session:
        session_id = "s"

        async def read_file(self, _path):
            return b"[" * 100_000

    with pytest.raises(ValueError, match="metadata is invalid"):
        await read_generation_state(
            _Session(), "/out.png", workspace_id=7, secret_key="secret"
        )

    payload = b"[" * 100_000
    signed = base64.urlsafe_b64encode(payload).decode()
    token = f"{signed}.{selection._sign(payload, 'secret')}"
    with pytest.raises(ValueError, match="token is invalid"):
        read_selection_token(token, workspace_id=7, thread_id=11, secret_key="secret")
