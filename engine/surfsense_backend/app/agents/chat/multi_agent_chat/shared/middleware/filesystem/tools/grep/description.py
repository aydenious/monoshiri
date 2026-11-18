"""Description string for ``grep``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Search for a literal text pattern across files.

Searches both your in-memory edits and the indexed chunks in Postgres.
State-cached file matches include real line numbers; database hits return
`line=0` because their position depends on per-document XML layout — call
`read_file(path)` afterwards to find the exact line.
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
