"""``_estimate_pages_safe`` falls back to the file size whenever the estimate fails."""

from __future__ import annotations

import pytest

from app.tasks.document_processors import file_processors

pytestmark = pytest.mark.unit


def test_estimate_falls_back_to_the_file_size(tmp_path):
    f = tmp_path / "scan.bin"
    f.write_bytes(b"x" * (200 * 1024))
    assert file_processors._estimate_pages_safe(str(f)) == 2


def test_estimate_falls_back_when_the_estimator_raises(tmp_path, monkeypatch):
    """ValueError is what the estimator raises for a missing/unreadable file."""
    f = tmp_path / "scan.bin"
    f.write_bytes(b"x" * (200 * 1024))

    def missing(_path):
        raise ValueError("File not found")

    monkeypatch.setattr(file_processors, "estimate_pages_before_processing", missing)
    assert file_processors._estimate_pages_safe(str(f)) == 2


def test_estimate_falls_back_on_a_corrupt_pdf(tmp_path, monkeypatch):
    """pypdf raises AttributeError/TypeError on some corrupt PDFs, outside PyPdfError."""
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"x" * (200 * 1024))

    def corrupt(_path):
        raise AttributeError("'NumberObject' object has no attribute 'get'")

    monkeypatch.setattr(file_processors, "estimate_pages_before_processing", corrupt)
    assert file_processors._estimate_pages_safe(str(f)) == 2
