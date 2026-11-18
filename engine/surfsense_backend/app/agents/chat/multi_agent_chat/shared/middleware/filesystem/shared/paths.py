"""Stateless path utilities shared by the middleware class and tool factories."""

from __future__ import annotations

import re

TEMP_PREFIX = "temp_"


def normalize_absolute_path(candidate: str) -> str:
    """Collapse slashes / backslashes and force an absolute path."""
    normalized = re.sub(r"/+", "/", candidate.strip().replace("\\", "/"))
    if not normalized:
        return "/"
    if normalized.startswith("/"):
        return normalized
    return f"/{normalized.lstrip('/')}"


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_ancestor_of(candidate: str, target: str) -> bool:
    """True iff ``candidate`` is a strict-or-equal ancestor of ``target``."""
    if candidate == "/":
        return target != "/"
    cand = candidate.rstrip("/")
    return target == cand or target.startswith(cand + "/")
