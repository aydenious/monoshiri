"""Description string for ``ls``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Lists files and directories at the given path.

Usage:
- Provide an absolute path under `/documents` (relative paths resolve under
  the current cwd, which defaults to `/documents`).
- For very large folders, use `offset` and `limit` to paginate the listing.
- Returns one entry per line; directories end with a trailing `/`.
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
