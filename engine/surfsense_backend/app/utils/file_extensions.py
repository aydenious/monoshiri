"""Per-parser document extension sets for the ETL pipeline.

Every consumer (file_classifier, connector-level skip checks, ETL pipeline
validation) imports from here so there is a single source of truth.

Extensions already covered by PLAINTEXT_EXTENSIONS, AUDIO_EXTENSIONS, or
DIRECT_CONVERT_EXTENSIONS in file_classifier are NOT repeated here -- these
sets are exclusively for the "document" ETL path (Docling / LlamaParse /
Unstructured).

Image extensions intentionally remain in the per-parser sets for fallback
compatibility.  IMAGE_EXTENSIONS is used only for routing classification.
"""

from pathlib import PurePosixPath

# ---------------------------------------------------------------------------
# Image extensions (used by file_classifier for routing to vision LLM)
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".svg",
        ".heic",
        ".heif",
    }
)

# Suffixes treated as images when deciding whether the vision LLM (not
# MinerU) should convert a file. Controller ruling R2: IMAGE_EXTENSIONS plus
# the HEIC/HEIF/TIFF spellings, lower-cased.
IMAGE_FILE_SUFFIXES: frozenset[str] = IMAGE_EXTENSIONS | {".heic", ".heif", ".tif"}

# ---------------------------------------------------------------------------
# Per-parser document extension sets (from official documentation)
# ---------------------------------------------------------------------------

DOCLING_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".tiff",
        ".tif",
        ".bmp",
        ".webp",
    }
)

LLAMAPARSE_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".docm",
        ".dot",
        ".dotm",
        ".pptm",
        ".pot",
        ".potx",
        ".xlsm",
        ".xlsb",
        ".xlw",
        ".rtf",
        ".epub",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".webp",
        ".svg",
        ".odt",
        ".ods",
        ".odp",
        ".hwp",
        ".hwpx",
    }
)

# MinerU 4.x accepts these suffixes natively (images are converted to PDF
# inside MinerU; HEIC/HEIF reach it as PNG after `app.conversion.heic`).
MINERU_NATIVE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".ppt",
        ".xlsx",
        ".xls",
        ".odt",
        ".odp",
        ".ods",
        ".epub",
        ".rtf",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".gif",
        ".tiff",
        ".tif",
        ".webp",
        ".jp2",
        ".heic",
        ".heif",
    }
)

UNSTRUCTURED_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".tif",
        ".heic",
        ".rtf",
        ".epub",
        ".odt",
        ".eml",
        ".msg",
        ".p7s",
    }
)

AZURE_DI_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".tiff",
        ".tif",
        ".heif",
    }
)

# ---------------------------------------------------------------------------
# Union (used by classify_file for routing) + service lookup
# ---------------------------------------------------------------------------

DOCUMENT_EXTENSIONS: frozenset[str] = (
    DOCLING_DOCUMENT_EXTENSIONS
    | LLAMAPARSE_DOCUMENT_EXTENSIONS
    | UNSTRUCTURED_DOCUMENT_EXTENSIONS
    | AZURE_DI_DOCUMENT_EXTENSIONS
    | MINERU_NATIVE_EXTENSIONS
)

# MinerU takes its native formats; Docling remains the fallback for anything it
# does not take (and the whole set when ETL_SERVICE=DOCLING).
MINERU_DOCUMENT_EXTENSIONS: frozenset[str] = (
    MINERU_NATIVE_EXTENSIONS | DOCLING_DOCUMENT_EXTENSIONS
)

_SERVICE_MAP: dict[str, frozenset[str]] = {
    "DOCLING": DOCLING_DOCUMENT_EXTENSIONS,
    "LLAMACLOUD": LLAMAPARSE_DOCUMENT_EXTENSIONS,
    "UNSTRUCTURED": UNSTRUCTURED_DOCUMENT_EXTENSIONS,
    "MINERU": MINERU_DOCUMENT_EXTENSIONS,
}


def get_document_extensions_for_service(etl_service: str | None) -> frozenset[str]:
    """Return the document extensions supported by *etl_service*.

    When *etl_service* is ``LLAMACLOUD`` and Azure Document Intelligence
    credentials are configured, the set is dynamically expanded to include
    Azure DI's supported extensions (e.g. ``.heif``).

    Falls back to the full union when the service is ``None`` or unknown.
    """
    extensions = _SERVICE_MAP.get(etl_service or "", DOCUMENT_EXTENSIONS)
    if etl_service == "LLAMACLOUD":
        from app.config import config as app_config

        if getattr(app_config, "AZURE_DI_ENDPOINT", None) and getattr(
            app_config, "AZURE_DI_KEY", None
        ):
            extensions = extensions | AZURE_DI_DOCUMENT_EXTENSIONS
    return extensions


def mineru_handles(filename: str) -> bool:
    """Return True when MinerU is the configured converter and accepts *filename*."""
    from app.config import config as app_config

    if (getattr(app_config, "ETL_SERVICE", None) or "") != "MINERU":
        return False
    return PurePosixPath(filename).suffix.lower() in MINERU_NATIVE_EXTENSIONS


def is_image_filename(filename: str) -> bool:
    """Return True when *filename* is an image for vision-LLM routing."""
    return PurePosixPath(filename).suffix.lower() in IMAGE_FILE_SUFFIXES


def is_supported_document_extension(filename: str) -> bool:
    """Return True if the file's extension is in the supported document set."""
    suffix = PurePosixPath(filename).suffix.lower()
    return suffix in DOCUMENT_EXTENSIONS
