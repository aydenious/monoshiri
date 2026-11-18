"""Description string for ``cd``."""

from __future__ import annotations

_DESCRIPTION = """Changes the current working directory (cwd).

Args:
- path: absolute or relative directory path. Relative paths resolve against
  the current cwd.

The new cwd is used by other filesystem tools whenever a relative path is
given. Returns the resolved cwd.
"""


def select_description() -> str:
    return _DESCRIPTION
