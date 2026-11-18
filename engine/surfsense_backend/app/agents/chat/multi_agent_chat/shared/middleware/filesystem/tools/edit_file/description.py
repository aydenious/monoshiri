"""Description string for ``edit_file``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Performs exact string replacements in files.

IMPORTANT:
- Read the file before editing.
- Preserve exact indentation and formatting.
- Edits to documents under `/documents/` are persisted at end of turn.
- Edits to `temp_*` files are discarded at end of turn.
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
