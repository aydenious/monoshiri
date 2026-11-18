"""Description string for ``mkdir``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Creates a directory under `/documents/`.

Stages the folder for end-of-turn commit; the Folder row is inserted only
after the agent's turn finishes successfully.

Args:
- path: absolute path of the new directory (must start with
  `/documents/`).

Notes:
- Parent folders are created as needed.
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
