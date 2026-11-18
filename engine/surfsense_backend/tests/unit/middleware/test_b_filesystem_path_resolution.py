"""Path/cwd/namespace tests for the LIVE cloud filesystem.

Ported from the former ``tests/unit/middleware/test_filesystem_middleware.py``
(which exercised the dead twin ``app.agents.chat.shared.middleware.filesystem``).
This drives the production free functions in
``app.agents.chat.multi_agent_chat.shared.middleware.filesystem.middleware``
instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.middleware.mode import (
    default_cwd,
)
from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.middleware.namespace_policy import (
    check_cloud_write_namespace,
)
from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.middleware.path_resolution import (
    current_cwd,
    get_contract_suggested_path,
    resolve_relative,
)

pytestmark = pytest.mark.unit


def _runtime(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=state or {})


# ---------------------------------------------------------------------------
# cwd defaults
# ---------------------------------------------------------------------------


class TestCwdDefaults:
    def test_default_cwd_is_documents_root(self):
        assert default_cwd() == "/documents"

    def test_current_cwd_uses_state_when_set(self):
        assert current_cwd(_runtime({"cwd": "/documents/notes"})) == "/documents/notes"

    def test_current_cwd_falls_back_to_default(self):
        assert current_cwd(_runtime({})) == "/documents"

    def test_current_cwd_ignores_invalid(self):
        assert current_cwd(_runtime({"cwd": "not-absolute"})) == "/documents"


# ---------------------------------------------------------------------------
# relative resolution
# ---------------------------------------------------------------------------


class TestRelativePathResolution:
    def test_relative_path_resolves_against_cwd(self):
        assert (
            resolve_relative("notes.md", _runtime({"cwd": "/documents/projects"}))
            == "/documents/projects/notes.md"
        )

    def test_relative_path_with_dotdot(self):
        assert (
            resolve_relative("../c.md", _runtime({"cwd": "/documents/a/b"}))
            == "/documents/a/c.md"
        )

    def test_absolute_path_is_kept(self):
        assert (
            resolve_relative("/other/x.md", _runtime({"cwd": "/documents"}))
            == "/other/x.md"
        )

    def test_empty_path_returns_cwd(self):
        assert (
            resolve_relative("", _runtime({"cwd": "/documents/projects"}))
            == "/documents/projects"
        )


# ---------------------------------------------------------------------------
# contract suggested-path fallback
# ---------------------------------------------------------------------------


class TestContractSuggestedPath:
    def test_falls_back_to_documents_notes_md(self):
        suggested = get_contract_suggested_path(
            _runtime({"file_operation_contract": {}}),
        )
        assert suggested == "/documents/notes.md"


# ---------------------------------------------------------------------------
# cloud write-namespace policy
# ---------------------------------------------------------------------------


class TestCloudWriteNamespacePolicy:
    def test_documents_path_allowed(self):
        assert check_cloud_write_namespace("/documents/foo.md", _runtime()) is None

    def test_documents_root_allowed(self):
        assert check_cloud_write_namespace("/documents", _runtime()) is None

    def test_temp_basename_anywhere_allowed(self):
        assert check_cloud_write_namespace("/temp_scratch.md", _runtime()) is None
        assert check_cloud_write_namespace("/foo/temp_x.md", _runtime()) is None
        assert check_cloud_write_namespace("/documents/temp_x.md", _runtime()) is None

    def test_other_paths_rejected(self):
        err = check_cloud_write_namespace("/foo/bar.md", _runtime())
        assert err is not None
        assert "must target /documents" in err

    def test_anon_doc_path_is_read_only(self):
        runtime = _runtime(
            {
                "kb_anon_doc": {
                    "path": "/documents/uploaded.xml",
                    "title": "uploaded",
                    "content": "",
                    "chunks": [],
                }
            }
        )
        err = check_cloud_write_namespace("/documents/uploaded.xml", runtime)
        assert err is not None
        assert "read-only" in err
