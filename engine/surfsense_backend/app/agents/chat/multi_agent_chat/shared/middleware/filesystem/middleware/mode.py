"""The filesystem's default working directory."""

from __future__ import annotations

from app.knowledge_store.paths import DOCUMENTS_ROOT


def default_cwd() -> str:
    return DOCUMENTS_ROOT
