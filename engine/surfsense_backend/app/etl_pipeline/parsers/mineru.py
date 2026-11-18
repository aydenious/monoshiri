"""Whole-file MinerU conversion for callers outside the upload progress path.

The upload pipeline drives MinerU itself, batch by batch, so it can persist
progress and resume (see ``app.conversion``). This parser exists for callers
that only need "file -> Markdown" (the local-folder indexer) and converts the
whole file in one MinerU task with no progress reporting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import time
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)

# Polling tolerates this many consecutive transport failures before giving up;
# a five-minute MinerU restart should not fail a document that is half parsed.
_MAX_CONSECUTIVE_POLL_FAILURES = 3


def _upload_name(filename: str, *, converted: bool) -> str:
    """Upload name with a lower-cased suffix (MinerU is case-sensitive)."""
    name = PurePosixPath(filename).name
    suffix = PurePosixPath(name).suffix.lower()
    stem = name[: -len(suffix)] if suffix else name
    if converted:
        return f"{stem}.png"
    if suffix == ".tif":
        # MinerU names its image suffixes ".tiff"; be explicit rather than guess.
        suffix = ".tiff"
    return f"{stem}{suffix}"


async def parse_with_mineru(file_path: str, filename: str) -> str:
    """Convert *file_path* with MinerU and return its stripped Markdown."""
    from app.config import config
    from app.conversion.heic import heic_to_png
    from app.conversion.mineru_client import (
        MinerUError,
        MinerUUnavailable,
        client_from_config,
    )

    source = Path(file_path)
    suffix = PurePosixPath(filename).suffix.lower()
    scratch: Path | None = None
    try:
        upload_path = source
        converted = suffix in {".heic", ".heif"}
        if converted:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                scratch = Path(tmp.name)
            heic_to_png(source, scratch)
            upload_path = scratch

        client = client_from_config()
        task_id = await client.submit(
            upload_path, _upload_name(filename, converted=converted), page_range=None
        )

        deadline = time.monotonic() + float(config.MINERU_GIVE_UP_SECONDS)
        poll_failures = 0
        while True:
            try:
                state = await client.get(task_id)
            except MinerUUnavailable:
                poll_failures += 1
                if (
                    poll_failures > _MAX_CONSECUTIVE_POLL_FAILURES
                    or time.monotonic() >= deadline
                ):
                    with contextlib.suppress(Exception):
                        await client.cancel(task_id)
                    raise
                await asyncio.sleep(float(config.MINERU_POLL_SECONDS))
                continue
            poll_failures = 0
            if state.status in ("completed", "partial"):
                if state.status == "partial":
                    logger.warning(
                        "MinerU task %s finished partially; indexing the pages "
                        "it returned",
                        task_id,
                    )
                if not state.result_url:
                    raise MinerUError(
                        "invalid_response",
                        f"MinerU task {task_id} finished without a result",
                    )
                return await client.fetch_markdown(state.result_url)
            if time.monotonic() >= deadline:
                await client.cancel(task_id)
                raise MinerUError(
                    "timeout",
                    f"MinerU conversion of {filename} did not finish within "
                    f"{config.MINERU_GIVE_UP_SECONDS}s",
                )
            await asyncio.sleep(float(config.MINERU_POLL_SECONDS))
    finally:
        if scratch is not None:
            with contextlib.suppress(OSError):
                scratch.unlink()
