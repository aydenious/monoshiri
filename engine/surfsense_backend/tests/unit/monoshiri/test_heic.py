"""HEIC/HEIF decoding for the upload path."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.conversion.heic import heic_to_png

pytestmark = pytest.mark.unit

# A tiny 8x4 HEIF container (AV1-coded), generated once with ffmpeg so the test
# needs no HEIF encoder at run time (pi-heif wheels ship decoders only). This
# proves the plumbing only: real iPhone HEVC + EXIF orientation is verified on
# the test stack in Task 7, not by this AV1 fixture.
_FIXTURE = Path(__file__).parent / "fixtures" / "photo.heif"


def test_heic_to_png_roundtrip(tmp_path):
    src = tmp_path / "photo.heic"
    shutil.copyfile(_FIXTURE, src)

    dst = tmp_path / "photo.png"
    heic_to_png(src, dst)

    with Image.open(dst) as image:
        assert image.size == (8, 4)
        assert image.format == "PNG"


def test_heic_garbage_raises_value_error(tmp_path):
    src = tmp_path / "broken.heic"
    src.write_bytes(b"definitely not a HEIC file")

    with pytest.raises(ValueError, match="Cannot read HEIC image") as excinfo:
        heic_to_png(src, tmp_path / "out.png")
    # The reason crosses the API boundary; it must not carry the server path.
    assert str(excinfo.value) == "Cannot read HEIC image"
