"""Resolve user-supplied paths to absolute paths the backends accept."""

from __future__ import annotations

import posixpath

from langchain.tools import ToolRuntime

from app.agents.chat.multi_agent_chat.shared.state.filesystem_state import (
    SurfSenseFilesystemState,
)

from ..shared.paths import normalize_absolute_path
from .mode import default_cwd


def current_cwd(
    runtime: ToolRuntime[None, SurfSenseFilesystemState],
) -> str:
    cwd = runtime.state.get("cwd") if hasattr(runtime, "state") else None
    if isinstance(cwd, str) and cwd.startswith("/"):
        return cwd
    return default_cwd()


def get_contract_suggested_path(
    runtime: ToolRuntime[None, SurfSenseFilesystemState],
) -> str:
    """Read the planner's suggested write path; otherwise default to ``notes.md``."""
    contract = runtime.state.get("file_operation_contract") or {}
    suggested = contract.get("suggested_path")
    if isinstance(suggested, str) and suggested.strip():
        return normalize_absolute_path(suggested)
    return default_cwd().rstrip("/") + "/notes.md"


def resolve_relative(
    path: str,
    runtime: ToolRuntime[None, SurfSenseFilesystemState],
) -> str:
    """Resolve ``path`` against cwd (no-op if already absolute)."""
    candidate = path.strip()
    if not candidate:
        return current_cwd(runtime)
    if candidate.startswith("/"):
        return normalize_absolute_path(candidate)
    cwd = current_cwd(runtime)
    joined = posixpath.normpath(posixpath.join(cwd, candidate))
    return normalize_absolute_path(joined)


def resolve_write_target_path(
    file_path: str,
    runtime: ToolRuntime[None, SurfSenseFilesystemState],
) -> str:
    """Empty → contract suggestion; otherwise cwd-relative."""
    candidate = file_path.strip()
    if not candidate:
        return get_contract_suggested_path(runtime)
    return resolve_relative(candidate, runtime)


def resolve_move_target_path(
    file_path: str,
    runtime: ToolRuntime[None, SurfSenseFilesystemState],
) -> str:
    """Empty → empty (caller validates); otherwise cwd-relative."""
    candidate = file_path.strip()
    if not candidate:
        return ""
    return resolve_relative(candidate, runtime)


def resolve_list_target_path(
    path: str,
    runtime: ToolRuntime[None, SurfSenseFilesystemState],
) -> str:
    """Root stays root; otherwise cwd-relative."""
    candidate = path.strip() or current_cwd(runtime)
    if candidate == "/":
        return "/"
    return resolve_relative(candidate, runtime)
