"""CLI: python -m app.monoshiri invite [--note TEXT] [--days N]"""

from __future__ import annotations

import argparse
import sys

from app.config import config
from app.monoshiri.invites import create_invite


def build_invite_url(token: str) -> str:
    base = (config.NEXT_FRONTEND_URL or "").rstrip("/")
    return f"{base}/register?invite={token}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.monoshiri")
    sub = parser.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("invite", help="mint a one-time signup invite link")
    inv.add_argument("--note", default="")
    inv.add_argument("--days", type=float, default=None)
    args = parser.parse_args(argv)
    if args.days is not None and args.days <= 0:
        print("error: --days must be greater than 0", file=sys.stderr)
        sys.exit(2)
    if not (config.NEXT_FRONTEND_URL or "").strip():
        print(
            "error: NEXT_FRONTEND_URL is not set; cannot build an invite URL",
            file=sys.stderr,
        )
        sys.exit(1)
    ttl = int(args.days * 86400) if args.days is not None else None
    print(build_invite_url(create_invite(ttl_seconds=ttl)))
    if args.note:
        # Invite tokens carry no metadata; CLI invites are not listed or revocable.
        print(f"note (not stored): {args.note}", file=sys.stderr)


if __name__ == "__main__":
    main()
