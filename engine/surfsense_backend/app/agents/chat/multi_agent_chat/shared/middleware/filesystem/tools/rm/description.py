"""Description string for ``rm``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Deletes a single file under `/documents/`.

Mirrors POSIX `rm path` (no `-r`, no glob expansion). Stages the deletion
for end-of-turn commit; the row is removed only after the agent's turn
finishes successfully.

Args:
- path: absolute or relative file path. Cannot point at a directory — use
  `rmdir` for empty folders. Cannot target the root or `/documents`.

Notes:
- The action is reversible via the per-action revert flow when action
  logging is enabled.
- The anonymous uploaded document is read-only and cannot be deleted.
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
