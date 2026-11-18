"""
File document processors orchestrating content extraction and indexing.

Delegates content extraction to the cache-aware ``extract_with_cache`` facade
(over ``EtlPipelineService``) and keeps only orchestration concerns
(notifications, logging, page estimates, saving).
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Document, Log
from app.etl_pipeline.page_estimate import estimate_pages_before_processing
from app.notifications.persistence import Notification
from app.notifications.service import NotificationService
from app.services.task_logging_service import TaskLoggingService

from ._helpers import update_document_from_connector
from ._save import save_file_document
from .markdown_processor import add_received_markdown_file_document

__all__ = [
    "process_file_in_background",
    "process_file_in_background_with_document",
    "save_file_document",
]


# ---------------------------------------------------------------------------
# Processing context (bundles parameters shared across handler functions)
# ---------------------------------------------------------------------------


@dataclass
class _ProcessingContext:
    session: AsyncSession
    file_path: str
    filename: str
    workspace_id: int
    user_id: str
    task_logger: TaskLoggingService
    log_entry: Log
    connector: dict | None = None
    notification: Notification | None = None
    use_vision_llm: bool = False
    processing_mode: str = "basic"


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------


async def _notify(
    ctx: _ProcessingContext,
    stage: str,
    stage_message: str | None = None,
    **kwargs,
) -> None:
    """Send a processing-progress notification if one is attached."""
    if not ctx.notification:
        return
    await NotificationService.document_processing.notify_processing_progress(
        ctx.session,
        ctx.notification,
        stage=stage,
        stage_message=stage_message,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Page-estimate helpers
# ---------------------------------------------------------------------------


def _estimate_pages_safe(file_path: str) -> int:
    """Estimate page count with a file-size fallback."""
    try:
        return estimate_pages_before_processing(file_path)
    except Exception:  # pypdf raises assorted non-PyPdfError types on corrupt files
        file_size = os.path.getsize(file_path)
        return max(1, file_size // (80 * 1024))


# ===================================================================
# Handlers for process_file_in_background (legacy / connector path)
# ===================================================================


async def _process_non_document_upload(ctx: _ProcessingContext) -> Document | None:
    """Extract content from a non-document file (plaintext/direct_convert/audio/image) via the unified ETL pipeline."""
    from app.etl_pipeline.cache import extract_with_cache
    from app.etl_pipeline.etl_document import EtlRequest

    await _notify(ctx, "parsing", "Processing file")
    await ctx.task_logger.log_task_progress(
        ctx.log_entry,
        f"Processing file: {ctx.filename}",
        {"processing_stage": "extracting"},
    )

    # Fetch the vision LLM whenever the operator opts in. The ETL
    # pipeline decides what to do with it: image files run through the
    # vision LLM directly; document files (PDFs) get per-image
    # descriptions appended via picture_describer.
    vision_llm = None
    if ctx.use_vision_llm:
        from app.services.llm_service import get_vision_llm

        vision_llm = await get_vision_llm(ctx.session, ctx.workspace_id)

    etl_result = await extract_with_cache(
        EtlRequest(file_path=ctx.file_path, filename=ctx.filename),
        vision_llm=vision_llm,
    )

    with contextlib.suppress(Exception):
        os.unlink(ctx.file_path)

    await _notify(ctx, "chunking")

    result = await add_received_markdown_file_document(
        ctx.session,
        ctx.filename,
        etl_result.markdown_content,
        ctx.workspace_id,
        ctx.user_id,
        ctx.connector,
    )
    if ctx.connector:
        await update_document_from_connector(result, ctx.connector, ctx.session)

    if result:
        await ctx.task_logger.log_task_success(
            ctx.log_entry,
            f"Successfully processed file: {ctx.filename}",
            {
                "document_id": result.id,
                "content_hash": result.content_hash,
                "file_type": etl_result.content_type,
                "etl_service": etl_result.etl_service,
            },
        )
    else:
        await ctx.task_logger.log_task_success(
            ctx.log_entry,
            f"File already exists (duplicate): {ctx.filename}",
            {"duplicate_detected": True, "file_type": etl_result.content_type},
        )
    return result


# ---------------------------------------------------------------------------
# Document file processing (ETL service dispatch)
# ---------------------------------------------------------------------------


async def _process_document_upload(ctx: _ProcessingContext) -> Document | None:
    """Route a document file to the configured ETL service via the unified pipeline."""
    from app.etl_pipeline.cache import extract_with_cache
    from app.etl_pipeline.etl_document import EtlRequest, ProcessingMode

    mode = ProcessingMode.coerce(ctx.processing_mode)
    estimated_pages = _estimate_pages_safe(ctx.file_path)

    await ctx.task_logger.log_task_progress(
        ctx.log_entry,
        f"Estimated {estimated_pages} pages for file: {ctx.filename}",
        {
            "estimated_pages": estimated_pages,
            "processing_mode": mode.value,
            "file_type": "document",
        },
    )

    await _notify(ctx, "parsing", "Extracting content")

    # Document files (PDF, docx, etc.) get vision LLM treatment too:
    # the ETL pipeline appends a per-image description section when
    # vision_llm is provided. See picture_describer.describe_pictures.
    vision_llm = None
    if ctx.use_vision_llm:
        from app.services.llm_service import get_vision_llm

        vision_llm = await get_vision_llm(ctx.session, ctx.workspace_id)

    etl_result = await extract_with_cache(
        EtlRequest(
            file_path=ctx.file_path,
            filename=ctx.filename,
            estimated_pages=estimated_pages,
            processing_mode=mode,
        ),
        vision_llm=vision_llm,
    )

    with contextlib.suppress(Exception):
        os.unlink(ctx.file_path)

    await _notify(ctx, "chunking")

    result = await save_file_document(
        ctx.session,
        ctx.filename,
        etl_result.markdown_content,
        ctx.workspace_id,
        ctx.user_id,
        etl_result.etl_service,
        ctx.connector,
    )

    if result:
        if ctx.connector:
            await update_document_from_connector(result, ctx.connector, ctx.session)
        await ctx.task_logger.log_task_success(
            ctx.log_entry,
            f"Successfully processed file: {ctx.filename}",
            {
                "document_id": result.id,
                "content_hash": result.content_hash,
                "file_type": "document",
                "etl_service": etl_result.etl_service,
                "pages_processed": estimated_pages,
                "processing_mode": mode.value,
            },
        )
    else:
        await ctx.task_logger.log_task_success(
            ctx.log_entry,
            f"Document already exists (duplicate): {ctx.filename}",
            {
                "duplicate_detected": True,
                "file_type": "document",
                "etl_service": etl_result.etl_service,
            },
        )
    return result


# ===================================================================
# Public orchestrators
# ===================================================================


async def process_file_in_background(
    file_path: str,
    filename: str,
    workspace_id: int,
    user_id: str,
    session: AsyncSession,
    task_logger: TaskLoggingService,
    log_entry: Log,
    connector: dict | None = None,
    notification: Notification | None = None,
    use_vision_llm: bool = False,
    processing_mode: str = "basic",
) -> Document | None:
    ctx = _ProcessingContext(
        session=session,
        file_path=file_path,
        filename=filename,
        workspace_id=workspace_id,
        user_id=user_id,
        task_logger=task_logger,
        log_entry=log_entry,
        connector=connector,
        notification=notification,
        use_vision_llm=use_vision_llm,
        processing_mode=processing_mode,
    )

    try:
        from app.etl_pipeline.file_classifier import (
            FileCategory as EtlFileCategory,
            classify_file as etl_classify,
        )

        category = etl_classify(filename)

        if category == EtlFileCategory.DOCUMENT:
            return await _process_document_upload(ctx)
        return await _process_non_document_upload(ctx)

    except Exception as e:
        await session.rollback()

        error_message = f"Failed to process file: {filename}"

        await task_logger.log_task_failure(
            log_entry,
            error_message,
            str(e),
            {"error_type": type(e).__name__, "filename": filename},
        )
        logging.error(f"Error processing file in background: {error_message}")
        raise


# ===================================================================
# 2-phase handler (process_file_in_background_with_document)
# ===================================================================


async def _extract_file_content(
    file_path: str,
    filename: str,
    workspace_id: int,
    session: AsyncSession,
    task_logger: TaskLoggingService,
    log_entry: Log,
    notification: Notification | None,
    use_vision_llm: bool = False,
    processing_mode: str = "basic",
    markdown_override: str | None = None,
) -> tuple[str, str]:
    """
    Extract markdown content from a file regardless of type.

    ``markdown_override`` is set when MinerU already produced the Markdown for
    this document (batched, resumable conversion): extraction is skipped and
    the override is returned as-is.

    Returns:
        Tuple of (markdown_content, etl_service_name).
    """
    if markdown_override is not None:
        # Still unlink: the upload temp file is ours to clean up.
        with contextlib.suppress(Exception):
            os.unlink(file_path)
        return markdown_override, "MINERU"

    from app.etl_pipeline.etl_document import EtlRequest, ProcessingMode
    from app.etl_pipeline.file_classifier import (
        FileCategory,
        classify_file as etl_classify,
    )

    mode = ProcessingMode.coerce(processing_mode)
    category = etl_classify(filename)
    estimated_pages = 0

    if notification:
        stage_messages = {
            FileCategory.PLAINTEXT: "Reading file",
            FileCategory.DIRECT_CONVERT: "Converting file",
            FileCategory.AUDIO: "Transcribing audio",
            FileCategory.IMAGE: "Analyzing image",
            FileCategory.UNSUPPORTED: "Unsupported file type",
            FileCategory.DOCUMENT: "Extracting content",
        }
        await NotificationService.document_processing.notify_processing_progress(
            session,
            notification,
            stage="parsing",
            stage_message=stage_messages.get(category, "Processing"),
        )

    await task_logger.log_task_progress(
        log_entry,
        f"Processing {category.value} file: {filename}",
        {"file_type": category.value, "processing_stage": "extracting"},
    )

    if category == FileCategory.DOCUMENT:
        estimated_pages = _estimate_pages_safe(file_path)

    # Vision LLM is provided to the ETL pipeline for any file category
    # when the operator opts in. Image files run through it directly;
    # document files (PDFs) get per-image descriptions appended via
    # picture_describer.
    vision_llm = None
    if use_vision_llm:
        from app.services.llm_service import get_vision_llm

        vision_llm = await get_vision_llm(session, workspace_id)

    from app.etl_pipeline.cache import extract_with_cache

    result = await extract_with_cache(
        EtlRequest(
            file_path=file_path,
            filename=filename,
            estimated_pages=estimated_pages,
            processing_mode=mode,
        ),
        vision_llm=vision_llm,
    )

    with contextlib.suppress(Exception):
        os.unlink(file_path)

    if not result.markdown_content or not result.markdown_content.strip():
        raise RuntimeError(f"Failed to extract content from file: {filename}")

    return result.markdown_content, result.etl_service


async def process_file_in_background_with_document(
    document: Document,
    file_path: str,
    filename: str,
    workspace_id: int,
    user_id: str,
    session: AsyncSession,
    task_logger: TaskLoggingService,
    log_entry: Log,
    connector: dict | None = None,
    notification: Notification | None = None,
    use_vision_llm: bool = False,
    processing_mode: str = "basic",
    markdown_override: str | None = None,
) -> Document | None:
    """
    Process file and update existing pending document (2-phase pattern).

    Phase 1 (API layer): Created document with pending status.
    Phase 2 (this function): Process file and update document to ready/failed.

    Indexing is deferred until after the dedup check so a duplicate upload
    never re-indexes content the workspace already has.
    """
    from app.indexing_pipeline.adapters.file_upload_adapter import (
        UploadDocumentAdapter,
    )
    from app.utils.document_converters import generate_content_hash

    from .base import check_duplicate_document

    doc_id = document.id

    try:
        markdown_content, etl_service = await _extract_file_content(
            file_path,
            filename,
            workspace_id,
            session,
            task_logger,
            log_entry,
            notification,
            use_vision_llm=use_vision_llm,
            processing_mode=processing_mode,
            markdown_override=markdown_override,
        )

        if not markdown_content:
            raise RuntimeError(f"Failed to extract content from file: {filename}")

        content_hash = generate_content_hash(markdown_content, workspace_id)
        existing_by_content = await check_duplicate_document(session, content_hash)
        if existing_by_content and existing_by_content.id != doc_id:
            logging.info(
                f"Duplicate content detected for {filename}, "
                f"matches document {existing_by_content.id}"
            )
            return None

        if notification:
            await NotificationService.document_processing.notify_processing_progress(
                session,
                notification,
                stage="chunking",
            )

        adapter = UploadDocumentAdapter(session)
        await adapter.index(
            markdown_content=markdown_content,
            filename=filename,
            etl_service=etl_service,
            workspace_id=workspace_id,
            user_id=user_id,
        )

        await task_logger.log_task_success(
            log_entry,
            f"Successfully processed file: {filename}",
            {
                "document_id": doc_id,
                "content_hash": content_hash,
                "file_type": etl_service,
                "processing_mode": processing_mode,
            },
        )
        return document

    except Exception as e:
        await session.rollback()

        error_message = f"Failed to process file: {filename}"

        await task_logger.log_task_failure(
            log_entry,
            error_message,
            str(e),
            {
                "error_type": type(e).__name__,
                "filename": filename,
                "document_id": doc_id,
            },
        )
        logging.error(f"Error processing file with document: {error_message}")
        raise
