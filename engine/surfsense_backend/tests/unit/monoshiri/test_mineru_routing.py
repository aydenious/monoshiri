"""ETL routing for ETL_SERVICE=MINERU, with Docling as the fallback parser."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from app.config import config
from app.conversion.mineru_client import MinerUUnavailable, TaskState
from app.etl_pipeline.etl_document import EtlRequest
from app.etl_pipeline.etl_pipeline_service import EtlPipelineService
from app.etl_pipeline.parsers import mineru as mineru_parser
from app.utils import file_extensions as fe

pytestmark = pytest.mark.unit


def test_mineru_extension_set_is_superset_of_docling():
    assert fe.MINERU_NATIVE_EXTENSIONS >= fe.DOCLING_DOCUMENT_EXTENSIONS
    assert ".pdf" in fe.MINERU_NATIVE_EXTENSIONS
    assert {".heic", ".heif"} <= fe.MINERU_NATIVE_EXTENSIONS

    mineru = fe.get_document_extensions_for_service("MINERU")
    assert mineru >= fe.MINERU_NATIVE_EXTENSIONS
    assert ".heic" in mineru
    assert ".pdf" in mineru


def test_mineru_handles_only_when_service_is_mineru(monkeypatch):
    monkeypatch.setattr(config, "ETL_SERVICE", "DOCLING")
    assert fe.mineru_handles("a.pdf") is False

    monkeypatch.setattr(config, "ETL_SERVICE", "MINERU")
    assert fe.mineru_handles("a.PDF") is True
    assert fe.mineru_handles("b.heic") is True
    assert fe.mineru_handles("c.html") is False
    assert fe.mineru_handles("d.md") is False


async def test_extract_document_uses_mineru_for_pdf_and_docling_for_others(
    monkeypatch,
):
    monkeypatch.setattr(config, "ETL_SERVICE", "MINERU")
    calls: list[tuple[str, str]] = []

    async def fake_mineru(file_path: str, filename: str) -> str:
        calls.append(("mineru", filename))
        return "# mineru"

    async def fake_docling(file_path: str, filename: str) -> str:
        calls.append(("docling", filename))
        return "# docling"

    monkeypatch.setattr(
        "app.etl_pipeline.parsers.mineru.parse_with_mineru", fake_mineru
    )
    monkeypatch.setattr(
        "app.etl_pipeline.parsers.docling.parse_with_docling", fake_docling
    )
    service = EtlPipelineService()

    pdf = await service._extract_document(
        EtlRequest(file_path="/tmp/x.pdf", filename="x.pdf")
    )
    assert pdf.markdown_content == "# mineru"
    assert pdf.etl_service == "MINERU"
    assert calls == [("mineru", "x.pdf")]

    # A type Docling still covers but MinerU does not: falls back to Docling.
    real_extensions = fe.get_document_extensions_for_service
    monkeypatch.setattr(
        fe,
        "get_document_extensions_for_service",
        lambda service: real_extensions(service) | {".html"},
    )
    html = await service._extract_document(
        EtlRequest(file_path="/tmp/x.html", filename="x.html")
    )
    assert html.markdown_content == "# docling"
    assert calls[-1] == ("docling", "x.html")

    # And the service switch itself restores Docling for everything.
    monkeypatch.setattr(config, "ETL_SERVICE", "DOCLING")
    calls.clear()
    switched = await service._extract_document(
        EtlRequest(file_path="/tmp/y.pdf", filename="y.pdf")
    )
    assert switched.markdown_content == "# docling"
    assert calls == [("docling", "y.pdf")]


# ---------------------------------------------------------------------------
# Web mirror + whole-file parser hardening (T3 review)
# ---------------------------------------------------------------------------

_WEB_EXTENSIONS = (
    Path(__file__).resolve().parents[4]
    / "surfsense_web"
    / "lib"
    / "supported-extensions.ts"
)


def _ts_block(text: str, marker: str) -> set[str]:
    start = text.index(marker)
    end = text.index("\n};", start)
    return set(re.findall(r'"(\.[a-z0-9]+)"', text[start:end]))


def _web_docling_extensions() -> set[str]:
    """Everything the web DOCLING entry offers (it spreads two dictionaries)."""
    text = _WEB_EXTENSIONS.read_text(encoding="utf-8")
    return (
        _ts_block(text, "const commonTypes")
        | _ts_block(text, "const audioFileTypes")
        | _ts_block(text, "const doclingTypes")
    )


def _web_mineru_extensions() -> set[str]:
    text = _WEB_EXTENSIONS.read_text(encoding="utf-8")
    start = text.index("MINERU: {")
    end = text.index("\n\t},", start)
    block = text[start:end]
    extensions = set(re.findall(r'"(\.[a-z0-9]+)"', block))
    if "...doclingTypes" in block:
        extensions |= _web_docling_extensions()
    return extensions


def test_web_mineru_list_mirrors_the_backend():
    """The picker must offer everything the backend accepts under MINERU.

    The plaintext/direct/audio paths run before ETL routing, so the picker
    needs DOCLING's non-document types as well as MinerU's own formats.
    """
    assert _web_mineru_extensions() >= _web_docling_extensions() | set(
        fe.get_document_extensions_for_service("MINERU")
    )


def test_backend_accepts_every_web_mineru_extension(monkeypatch):
    """Nothing the picker offers may be unroutable when ETL_SERVICE=MINERU."""
    from app.etl_pipeline.file_classifier import (
        FileCategory,
        classify_file,
        should_skip_for_service,
    )

    monkeypatch.setattr(config, "ETL_SERVICE", "MINERU")
    for extension in sorted(_web_mineru_extensions()):
        filename = f"upload{extension}"
        assert classify_file(filename) != FileCategory.UNSUPPORTED, filename
        assert should_skip_for_service(filename, "MINERU") is False, filename


def test_markdown_under_mineru_takes_the_plaintext_path(monkeypatch):
    """A .md upload must never be handed to MinerU (it needs no conversion)."""
    from app.etl_pipeline.file_classifier import FileCategory, classify_file
    from app.tasks.celery_tasks.document_tasks import _mineru_handoff_applies

    monkeypatch.setattr(config, "ETL_SERVICE", "MINERU")
    assert classify_file("notes.md") == FileCategory.PLAINTEXT
    assert _mineru_handoff_applies("notes.md", False) is False


def test_upload_name_lowercases_the_suffix():
    assert mineru_parser._upload_name("BOOK.PDF", converted=False) == "BOOK.pdf"
    assert mineru_parser._upload_name("photo.TIF", converted=False) == "photo.tiff"
    assert mineru_parser._upload_name("IMG.HEIC", converted=True) == "IMG.png"
    assert mineru_parser._upload_name("notes.docx", converted=False) == "notes.docx"
    assert mineru_parser._upload_name("no-extension", converted=False) == "no-extension"


class _FakeMinerU:
    def __init__(self, script) -> None:
        self.script = list(script)
        self.cancelled: list[str] = []

    async def submit(self, *_args, **_kwargs) -> str:
        return "t1"

    async def get(self, _task_id: str):
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def fetch_markdown(self, _result_url: str) -> str:
        return "# done"

    async def cancel(self, task_id: str) -> None:
        self.cancelled.append(task_id)


def _completed() -> TaskState:
    return TaskState(
        task_id="t1",
        status="completed",
        result_url="http://mineru-api:8000/v1/files/x/content",
    )


async def _parse(monkeypatch, tmp_path, fake):
    from app.config import config as app_config
    from app.conversion import mineru_client

    monkeypatch.setattr(app_config, "MINERU_POLL_SECONDS", 0.0)
    monkeypatch.setattr(mineru_client, "client_from_config", lambda: fake)
    source = tmp_path / "notes.txt"
    source.write_text("x")
    return await mineru_parser.parse_with_mineru(str(source), "notes.txt")


async def test_whole_file_parser_tolerates_transient_poll_failures(
    monkeypatch, tmp_path
):
    fake = _FakeMinerU(
        [
            MinerUUnavailable("unavailable", "blip"),
            MinerUUnavailable("unavailable", "blip"),
            MinerUUnavailable("unavailable", "blip"),
            _completed(),
        ]
    )

    assert await _parse(monkeypatch, tmp_path, fake) == "# done"
    assert fake.cancelled == []


async def test_whole_file_parser_fails_after_three_tolerated_failures(
    monkeypatch, tmp_path
):
    fake = _FakeMinerU([MinerUUnavailable("unavailable", "down")] * 4)

    with pytest.raises(MinerUUnavailable):
        await _parse(monkeypatch, tmp_path, fake)
    assert fake.cancelled == ["t1"]


async def test_whole_file_parser_warns_on_partial_result(monkeypatch, tmp_path, caplog):
    fake = _FakeMinerU(
        [
            TaskState(
                task_id="t1",
                status="partial",
                result_url="http://mineru-api:8000/v1/files/x/content",
            )
        ]
    )

    with caplog.at_level(logging.WARNING):
        assert await _parse(monkeypatch, tmp_path, fake) == "# done"
    assert "partially" in caplog.text
    assert "t1" in caplog.text
