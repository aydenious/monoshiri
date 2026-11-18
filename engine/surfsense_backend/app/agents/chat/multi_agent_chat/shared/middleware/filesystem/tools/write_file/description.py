"""Description string for ``write_file``."""

from __future__ import annotations

_CLOUD_DESCRIPTION = """Writes a new text file to the workspace.

Usage:
- Files written under `/documents/<...>` are persisted as Documents at end
  of turn.
- Use a `temp_` filename prefix (e.g. `temp_plan.md` or `/documents/temp_x.md`)
  for scratch/working files; they are automatically discarded at end of turn.
- Writes outside `/documents/` are rejected unless the basename starts with
  `temp_`.
- Supported outputs include common LLM-friendly text formats like markdown,
  json, yaml, csv, xml, html, css, sql, and code files.
- Avoid placeholders; produce concrete and useful text.
"""


def select_description() -> str:
    return _CLOUD_DESCRIPTION
