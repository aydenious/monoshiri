"""Rule: user-supplied URLs leave the backend only through ``safe_fetch``.

Every Monoshiri-owned module that handles a saved URL is walked with ``ast``.
A direct HTTP import there is a bypass of the SSRF guard, so the test fails —
unless the path is on the short, reasoned exception list below.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = BACKEND_ROOT / "app"

# Walked: everything under conversion/ plus every module on the URL-save path,
# the permitted client and the YouTube transcript fetcher. Adding a new module
# that fetches user URLs means adding it here.
_WALKED_GLOBS = (
    "app/conversion/**/*.py",
    "app/routes/url_documents_routes.py",
    "app/services/url_ingest.py",
    "app/tasks/celery_tasks/url_tasks.py",
    "app/utils/crawl/safe_fetch.py",
    "app/proprietary/platforms/youtube/subtitles.py",
)

FORBIDDEN_PREFIXES = (
    "httpx",
    "requests",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "http.client",
    # trafilatura ships its own urllib3-based downloader; using it would skip
    # the SSRF guard entirely. ``trafilatura.extract`` is fine.
    "trafilatura.downloads",
)

# Names that are a fetch even when reached as an attribute rather than an
# import (e.g. a lazily imported ``trafilatura`` module calling ``fetch_url``).
FORBIDDEN_ATTRIBUTES = frozenset({"fetch_url"})

EXCEPTIONS = {
    "app/conversion/mineru_client.py": (
        "talks to one fixed internal MinerU host; it refuses foreign URLs"
    ),
    "app/utils/crawl/safe_fetch.py": (
        "the single permitted HTTP client; this gate exists to keep every "
        "other module on it"
    ),
    "app/proprietary/platforms/youtube/subtitles.py": (
        "the YouTube transcript call: the fixed hosts are youtube.com and "
        "youtu.be, enforced by get_youtube_video_id, so the user controls "
        "only a video id, never a destination host"
    ),
}


def _walked_modules() -> list[Path]:
    modules: set[Path] = set()
    for pattern in _WALKED_GLOBS:
        modules.update(APP_ROOT.glob(pattern.removeprefix("app/")))
    return sorted(modules)


def _imported_names(source: str) -> set[str]:
    """Every import target, so ``from urllib import request`` is visible too."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                names.add(module)
            for alias in node.names:
                names.add(f"{module}.{alias.name}" if module else alias.name)
    return names


def _forbidden_imports(source: str) -> set[str]:
    hits: set[str] = set()
    for name in _imported_names(source):
        for prefix in FORBIDDEN_PREFIXES:
            if name == prefix or name.startswith(f"{prefix}."):
                hits.add(prefix)
    return hits


def _forbidden_attribute_calls(source: str) -> set[str]:
    """Fetch helpers reached as attributes (``mod.fetch_url(...)``)."""
    hits: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            hits.add(f".{node.attr}")
    return hits


def _forbidden(source: str) -> set[str]:
    return _forbidden_imports(source) | _forbidden_attribute_calls(source)


def test_monoshiri_owned_modules_do_not_fetch_directly():
    offenders: list[tuple[str, list[str]]] = []
    for path in _walked_modules():
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative in EXCEPTIONS:
            continue
        hits = _forbidden(path.read_text(encoding="utf-8"))
        if hits:
            offenders.append((relative, sorted(hits)))

    assert not offenders, (
        "direct HTTP imports in URL-handling modules bypass the SSRF guard; "
        f"use app.utils.crawl.safe_fetch: {offenders}"
    )


def test_walker_flags_a_direct_fetch_import():
    """Self-test: the walker is not vacuously green."""
    assert _forbidden("import requests\n") == {"requests"}
    assert _forbidden("import httpx as h\n") == {"httpx"}
    assert _forbidden("from urllib.request import urlopen\n") == {"urllib.request"}
    assert _forbidden("from urllib import request\n") == {"urllib.request"}
    assert _forbidden("import asyncio\nimport aiohttp\n") == {"aiohttp"}
    assert _forbidden("import urllib3\n") == {"urllib3"}
    assert _forbidden("from http.client import HTTPConnection\n") == {"http.client"}
    assert _forbidden("from http import client\n") == {"http.client"}
    assert _forbidden("import trafilatura\n") == set()
    assert _forbidden("import trafilatura\nx = trafilatura.fetch_url('u')\n") == {
        ".fetch_url"
    }
    assert _forbidden("from trafilatura.downloads import fetch_url\n") == {
        "trafilatura.downloads"
    }
    assert _forbidden("from trafilatura import downloads\n") == {
        "trafilatura.downloads"
    }
    assert _forbidden("import asyncio\n") == set()


def test_exceptions_exist_and_are_justified():
    walked = {path.relative_to(BACKEND_ROOT).as_posix() for path in _walked_modules()}
    for exempted in EXCEPTIONS:
        assert exempted in walked, f"dead exemption: {exempted} is not walked"
        assert EXCEPTIONS[exempted], f"missing reason for {exempted}"
    # The files whose fetch is deliberate are all covered.
    assert "app/utils/crawl/safe_fetch.py" in walked
    assert "app/tasks/celery_tasks/url_tasks.py" in walked
    assert "app/proprietary/platforms/youtube/subtitles.py" in walked
