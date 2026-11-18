"""Decode HEIC/HEIF photos (iPhone camera default) to PNG for MinerU.

MinerU 4.x accepts BMP/GIF/JP2/JPEG/PNG/TIFF/WebP images but not HEIC/HEIF, so
the server converts before submitting. Pillow gets HEIF support from
``pi-heif``; orientation is normalised from EXIF before the PNG is written.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def heic_to_png(src: Path, dst: Path) -> None:
    """Convert the HEIC/HEIF image at *src* to a PNG at *dst*."""
    import pi_heif
    from PIL import Image, ImageOps

    try:
        pi_heif.register_heif_opener()
        with Image.open(src) as image:
            image = ImageOps.exif_transpose(image)
            image.save(dst, format="PNG")
    except Exception:
        # The stored reason must be fixed text: the exception carries the
        # server-side path, which the API must not expose.
        logger.exception("cannot decode HEIC/HEIF image for conversion")
        raise ValueError("Cannot read HEIC image") from None
