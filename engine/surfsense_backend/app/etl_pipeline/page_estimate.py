"""Page-count estimation for an ETL request.

``EtlRequest.estimated_pages`` sizes the LlamaCloud job timeout
(:func:`app.etl_pipeline.constants.calculate_job_timeout`), so the upload
processor estimates the page count before extraction. Nothing here touches the
database or the wallet: it is a pure file/metadata reading.
"""

import logging
import os
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


def estimate_pages_from_metadata(
    file_name_or_ext: str, file_size: int | str | None = None
) -> int:
    """Size-based page estimation from file name/extension and byte size.

    Pure function — no file I/O, no database access.

    ``file_name_or_ext`` can be a full filename (``"report.pdf"``) or
    a bare extension (``".pdf"``).  ``file_size`` may be an int, a
    stringified int from a cloud API, or *None*.
    """
    if file_size is not None:
        try:
            file_size = int(file_size)
        except (ValueError, TypeError):
            file_size = 0
    else:
        file_size = 0

    if file_size <= 0:
        return 1

    ext = PurePosixPath(file_name_or_ext).suffix.lower() if file_name_or_ext else ""
    if not ext and file_name_or_ext.startswith("."):
        ext = file_name_or_ext.lower()
    file_ext = ext

    if file_ext == ".pdf":
        return max(1, file_size // (100 * 1024))

    if file_ext in {
        ".doc",
        ".docx",
        ".docm",
        ".dot",
        ".dotm",
        ".odt",
        ".ott",
        ".sxw",
        ".stw",
        ".uot",
        ".rtf",
        ".pages",
        ".wpd",
        ".wps",
        ".abw",
        ".zabw",
        ".cwk",
        ".hwp",
        ".lwp",
        ".mcw",
        ".mw",
        ".sdw",
        ".vor",
    }:
        return max(1, file_size // (50 * 1024))

    if file_ext in {
        ".ppt",
        ".pptx",
        ".pptm",
        ".pot",
        ".potx",
        ".odp",
        ".otp",
        ".sxi",
        ".sti",
        ".uop",
        ".key",
        ".sda",
        ".sdd",
        ".sdp",
    }:
        return max(1, file_size // (200 * 1024))

    if file_ext in {
        ".xls",
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".xlw",
        ".xlr",
        ".ods",
        ".ots",
        ".fods",
        ".numbers",
        ".123",
        ".wk1",
        ".wk2",
        ".wk3",
        ".wk4",
        ".wks",
        ".wb1",
        ".wb2",
        ".wb3",
        ".wq1",
        ".wq2",
        ".csv",
        ".tsv",
        ".slk",
        ".sylk",
        ".dif",
        ".dbf",
        ".prn",
        ".qpw",
        ".602",
        ".et",
        ".eth",
    }:
        return max(1, file_size // (100 * 1024))

    if file_ext in {".epub"}:
        return max(1, file_size // (50 * 1024))

    if file_ext in {".txt", ".log", ".md", ".markdown", ".htm", ".html", ".xml"}:
        return max(1, file_size // 3000)

    if file_ext in {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".tiff",
        ".webp",
        ".svg",
        ".cgm",
        ".odg",
        ".pbd",
    }:
        return 1

    if file_ext in {".mp3", ".m4a", ".wav", ".mpga"}:
        return max(1, file_size // (1024 * 1024))

    if file_ext in {".mp4", ".mpeg", ".webm"}:
        return max(1, file_size // (5 * 1024 * 1024))

    return max(1, file_size // (80 * 1024))


def estimate_pages_before_processing(file_path: str) -> int:
    """Estimate page count from a local file before processing.

    For PDFs, attempts to read the actual page count via pypdf. For everything
    else, delegates to :func:`estimate_pages_from_metadata`.
    """
    if not os.path.exists(file_path):
        raise ValueError(f"File not found: {file_path}")

    file_ext = Path(file_path).suffix.lower()
    file_size = os.path.getsize(file_path)

    if file_ext == ".pdf":
        try:
            import pypdf

            with open(file_path, "rb") as f:
                pdf_reader = pypdf.PdfReader(f)
                return len(pdf_reader.pages)
        except (OSError, ValueError, KeyError, pypdf.errors.PyPdfError) as exc:
            # Unreadable, encrypted or malformed PDF: size-based estimate below.
            logger.debug("pypdf could not read %s: %s", file_path, exc)

    return estimate_pages_from_metadata(file_ext, file_size)
