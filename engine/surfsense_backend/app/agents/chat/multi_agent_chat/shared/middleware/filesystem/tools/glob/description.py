"""Description string for ``glob``."""

from __future__ import annotations

_DESCRIPTION = """Find files matching a glob pattern.

Supports standard glob patterns: `*`, `**`, `?`.
Returns absolute file paths.
"""


def select_description() -> str:
    return _DESCRIPTION
