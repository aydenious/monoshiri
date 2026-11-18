"""Public assembly of the FS system prompt for a session."""

from __future__ import annotations

from .cloud import BODY
from .common import HEADER, SANDBOX_ADDENDUM


def build_system_prompt(*, sandbox_available: bool) -> str:
    """Assemble the FS prompt: common header + body + optional sandbox section."""
    base = HEADER + BODY
    if sandbox_available:
        base += SANDBOX_ADDENDUM
    return base
