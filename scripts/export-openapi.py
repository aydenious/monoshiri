#!/usr/bin/env python3
"""Export the frozen OpenAPI contract and its Swift copy.

The contract is generated from the app itself, with the environment pinned to
the values the schema is allowed to depend on, so a developer's ``.env`` cannot
change the committed file. Run from ``engine/surfsense_backend``:

    uv run python ../../scripts/export-openapi.py            # write
    uv run python ../../scripts/export-openapi.py --check    # drift check

``--check`` prints a unified diff (first 200 lines) and exits 1 on a difference;
CI runs it so an API change without a contract update fails the build.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "engine" / "surfsense_backend"
CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "openapi.json"
SWIFT_COPY_PATH = (
    REPO_ROOT
    / "clients"
    / "swift"
    / "MonoshiriAPI"
    / "Sources"
    / "MonoshiriAPI"
    / "openapi.json"
)

# The contract describes the LOCAL (email/password) deployment. These values are
# forced — not defaulted — before the app is imported so that the output is the
# same in CI, on a developer's machine with a populated .env, and under pytest.
CONTRACT_ENV = {
    "AUTH_TYPE": "LOCAL",
    "DATABASE_URL": "postgresql+asyncpg://x:x@127.0.0.1:1/x",
    "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2",
    "SECRET_KEY": "openapi-export-dummy",
    "REGISTRATION_ENABLED": "TRUE",
}

MAX_DIFF_LINES = 200


def _pin_environment() -> None:
    os.environ.update(CONTRACT_ENV)
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))


def render_contract() -> str:
    """Return the deterministic contract text for the current backend code.

    The pinned environment is restored afterwards so importing this module from
    a test session does not overwrite the ``DATABASE_URL`` and auth settings the
    other tests run under.
    """
    saved_environ = os.environ.copy()
    try:
        _pin_environment()
        from app.app import app

        return json.dumps(app.openapi(), sort_keys=True, indent=2) + "\n"
    finally:
        os.environ.clear()
        os.environ.update(saved_environ)


def write_contract(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _unified_diff(committed: str, generated: str) -> str:
    lines = list(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=str(CONTRACT_PATH.relative_to(REPO_ROOT)),
            tofile="regenerated",
        )
    )
    shown = "".join(lines[:MAX_DIFF_LINES])
    if len(lines) > MAX_DIFF_LINES:
        shown += f"\n... {len(lines) - MAX_DIFF_LINES} more diff lines ...\n"
    return shown


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare with the committed contract instead of writing it",
    )
    args = parser.parse_args(argv)

    generated = render_contract()

    if args.check:
        committed = (
            CONTRACT_PATH.read_text(encoding="utf-8") if CONTRACT_PATH.exists() else ""
        )
        if committed != generated:
            print(
                "API changed: run scripts/export-openapi.py and commit "
                f"{CONTRACT_PATH.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
            print(_unified_diff(committed, generated), file=sys.stderr)
            return 1
        # The generated Swift client compiles its own copy of the contract; it
        # must be the same bytes or the client silently targets an older API.
        swift_copy = (
            SWIFT_COPY_PATH.read_text(encoding="utf-8")
            if SWIFT_COPY_PATH.exists()
            else ""
        )
        if swift_copy != generated:
            print(
                "Swift client contract copy is stale: run "
                "scripts/export-openapi.py and commit "
                f"{SWIFT_COPY_PATH.relative_to(REPO_ROOT)}",
                file=sys.stderr,
            )
            print(_unified_diff(swift_copy, generated), file=sys.stderr)
            return 1
        print("OpenAPI contract is up to date.")
        return 0

    write_contract(CONTRACT_PATH, generated)
    write_contract(SWIFT_COPY_PATH, generated)
    print(
        "Wrote "
        f"{CONTRACT_PATH.relative_to(REPO_ROOT)} and "
        f"{SWIFT_COPY_PATH.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
