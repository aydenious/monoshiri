"""Description string for ``move_file``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Moves or renames a file or folder.

Use absolute paths for both source and destination.

Notes:
- `move_file` is staged this turn and committed at end of turn.
- The agent cannot overwrite an existing destination — pass a fresh dest
  path or move the existing destination away first.
- The anonymous uploaded document is read-only and cannot be moved.
- Rename is a special case of move (same folder, different filename).
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
