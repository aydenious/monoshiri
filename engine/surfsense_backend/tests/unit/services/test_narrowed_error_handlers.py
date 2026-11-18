"""Handlers narrowed from ``except Exception`` keep their fallbacks."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.local_folder import (
    LocalFolderBackend,
)
from app.services.llm_error_adapter import _extract_provider_status_code
from app.tasks.chat.streaming.errors.classifier import (
    _extract_provider_error_code,
    _parse_error_payload,
)

pytestmark = pytest.mark.unit


def test_status_code_skips_values_that_are_not_ints():
    parsed = {"code": "not-a-number", "status": [1], "error": {"code": "429"}}
    assert _extract_provider_status_code(parsed) == 429


def test_status_code_none_when_nothing_is_an_int():
    assert _extract_provider_status_code({"code": "x"}) is None


def test_error_code_skips_values_that_are_not_ints():
    assert _extract_provider_error_code({"code": "x", "error": {"code": 503}}) == 503


def test_error_code_none_when_nothing_is_an_int():
    assert _extract_provider_error_code({"code": {}}) is None


def test_parse_error_payload_skips_non_json_candidates():
    assert _parse_error_payload('oops {"code": 429}') == {"code": 429}
    assert _parse_error_payload("no braces here") is None


def test_parse_error_payload_skips_too_deeply_nested_json():
    assert _parse_error_payload("[" * 200_000) is None


def test_codes_skip_values_too_large_for_int():
    inf = float("inf")
    assert _extract_provider_status_code({"code": inf, "status": 429}) == 429
    assert _extract_provider_error_code({"code": inf, "error": {"code": 503}}) == 503


def test_grep_skips_a_file_that_cannot_be_read(tmp_path: Path, monkeypatch, caplog):
    (tmp_path / "ok.txt").write_text("needle here", encoding="utf-8")
    (tmp_path / "bad.txt").write_text("needle too", encoding="utf-8")
    real = Path.read_text

    def flaky(self, *a, **k):
        if self.name == "bad.txt":
            raise PermissionError("denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    backend = LocalFolderBackend(str(tmp_path))
    with caplog.at_level(logging.DEBUG):
        result = backend.grep_raw("needle", "/")
    paths = [m["path"] for m in result]
    assert paths == ["/ok.txt"]
    assert "could not be read" in caplog.text


def test_glob_skips_a_hit_that_cannot_be_resolved(tmp_path: Path, monkeypatch):
    (tmp_path / "ok.txt").write_text("a", encoding="utf-8")
    (tmp_path / "bad.txt").write_text("b", encoding="utf-8")
    real = Path.resolve

    def flaky(self, *a, **k):
        if self.name == "bad.txt":
            raise RuntimeError("Symlink loop")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", flaky)
    backend = LocalFolderBackend(str(tmp_path))
    paths = [m["path"] for m in backend.glob_info("*.txt", "/")]
    assert paths == ["/ok.txt"]
