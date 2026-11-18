"""
Local folder indexer.

Indexes files the browser uploaded from a folder picker:
- Folder structure mirroring into DB Folder rows from the uploaded paths
- Document versioning via create_version_snapshot
- ETL-based file parsing for binary formats (PDF, DOCX, images, audio, etc.)
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import (
    Document,
    DocumentStatus,
    DocumentType,
    Folder,
)
from app.indexing_pipeline.connector_document import ConnectorDocument
from app.indexing_pipeline.document_hashing import compute_identifier_hash
from app.indexing_pipeline.indexing_pipeline_service import IndexingPipelineService
from app.services.task_logging_service import TaskLoggingService
from app.utils.db_faults import DB_FAULTS
from app.utils.document_versioning import create_version_snapshot

logger = logging.getLogger(__name__)


async def check_document_by_unique_identifier(
    session: AsyncSession, unique_identifier_hash: str
) -> Document | None:
    """Return the document with this identifier hash, chunks eagerly loaded."""
    result = await session.execute(
        select(Document)
        .options(selectinload(Document.chunks))
        .where(Document.unique_identifier_hash == unique_identifier_hash)
    )
    return result.scalars().first()


HeartbeatCallbackType = Callable[[int], Awaitable[None]]


async def _read_file_content(
    file_path: str, filename: str, *, vision_llm=None, processing_mode: str = "basic"
) -> str:
    """Read file content via the unified ETL pipeline.

    All file types (plaintext, audio, direct-convert, document, image) are
    handled by ``EtlPipelineService``.
    """
    from app.etl_pipeline.cache import extract_with_cache
    from app.etl_pipeline.etl_document import EtlRequest, ProcessingMode

    mode = ProcessingMode.coerce(processing_mode)
    result = await extract_with_cache(
        EtlRequest(file_path=file_path, filename=filename, processing_mode=mode),
        vision_llm=vision_llm,
    )
    return result.markdown_content


def _content_hash(content: str, workspace_id: int) -> str:
    """SHA-256 hash of content scoped to a workspace.

    Matches the format used by ``compute_content_hash`` in the unified
    pipeline so that dedup checks are consistent.
    """
    import hashlib

    return hashlib.sha256(f"{workspace_id}:{content}".encode()).hexdigest()


def _compute_raw_file_hash(file_path: str) -> str:
    """SHA-256 hash of the raw file bytes.

    Much cheaper than ETL/OCR extraction -- only performs sequential I/O.
    Used as a pre-filter to skip expensive content extraction when the
    underlying file hasn't changed at all.
    """
    import hashlib

    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def _compute_file_content_hash(
    file_path: str,
    filename: str,
    workspace_id: int,
    *,
    vision_llm=None,
    processing_mode: str = "basic",
) -> tuple[str, str]:
    """Read a file (via ETL if needed) and compute its content hash.

    Returns (content_text, content_hash).
    """
    content = await _read_file_content(
        file_path, filename, vision_llm=vision_llm, processing_mode=processing_mode
    )
    return content, _content_hash(content, workspace_id)


async def _resolve_folder_for_file(
    session: AsyncSession,
    rel_path: str,
    root_folder_id: int,
    workspace_id: int,
    user_id: str,
) -> int:
    """Given a file's relative path, ensure all parent Folder rows exist and
    return the folder_id for the file's immediate parent directory.

    For a file at "notes/daily/today.md", this ensures Folder rows exist for
    "notes" and "notes/daily", and returns the id of "notes/daily".
    For a file at "readme.md" (root level), returns root_folder_id.
    """
    parent_dir = str(Path(rel_path).parent)
    if parent_dir == ".":
        return root_folder_id

    parts = Path(parent_dir).parts
    current_parent_id = root_folder_id

    for part in parts:
        existing = (
            await session.execute(
                select(Folder).where(
                    Folder.name == part,
                    Folder.parent_id == current_parent_id,
                    Folder.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()

        if existing:
            current_parent_id = existing.id
        else:
            new_folder = Folder(
                name=part,
                parent_id=current_parent_id,
                workspace_id=workspace_id,
                created_by_id=user_id,
                position="a0",
            )
            session.add(new_folder)
            await session.flush()
            current_parent_id = new_folder.id

    return current_parent_id


async def _set_indexing_flag(session: AsyncSession, folder_id: int) -> None:
    folder = await session.get(Folder, folder_id)
    if folder:
        meta = dict(folder.folder_metadata or {})
        meta["indexing_in_progress"] = True
        folder.folder_metadata = meta
        await session.commit()


async def _clear_indexing_flag(session: AsyncSession, folder_id: int) -> None:
    try:
        folder = await session.get(Folder, folder_id)
        if folder:
            meta = dict(folder.folder_metadata or {})
            meta.pop("indexing_in_progress", None)
            folder.folder_metadata = meta
            await session.commit()
    except DB_FAULTS:
        # A database failure (raw ones at connect time included) only costs
        # the flag, which expires anyway.
        logger.warning("Failed to clear indexing flag", exc_info=True)


def _build_connector_doc(
    title: str,
    content: str,
    relative_path: str,
    folder_name: str,
    *,
    workspace_id: int,
    user_id: str,
) -> ConnectorDocument:
    """Build a ConnectorDocument from a local file's extracted content."""
    unique_id = f"{folder_name}:{relative_path}"
    metadata = {
        "folder_name": folder_name,
        "file_path": relative_path,
        "document_type": "Local Folder File",
        "connector_type": "Local Folder",
    }

    return ConnectorDocument(
        title=title,
        source_markdown=content,
        unique_id=unique_id,
        document_type=DocumentType.LOCAL_FOLDER_FILE,
        workspace_id=workspace_id,
        connector_id=None,
        created_by_id=user_id,
        metadata=metadata,
    )


# ========================================================================
# Upload-based folder indexing (works for all deployment modes)
# ========================================================================


async def _mirror_folder_structure_from_paths(
    session: AsyncSession,
    relative_paths: list[str],
    folder_name: str,
    workspace_id: int,
    user_id: str,
    root_folder_id: int | None = None,
) -> tuple[dict[str, int], int]:
    """Create DB Folder rows from a list of relative file paths.

    It derives the directory tree from the paths provided by the client.

    Returns (mapping, root_folder_id) where mapping is
    relative_dir_path -> folder_id.  The empty-string key maps to root.
    """
    dir_set: set[str] = set()
    for rp in relative_paths:
        parent = str(Path(rp).parent)
        if parent == ".":
            continue
        parts = Path(parent).parts
        for i in range(len(parts)):
            dir_set.add(str(Path(*parts[: i + 1])))

    subdirs = sorted(dir_set, key=lambda p: p.count(os.sep))

    mapping: dict[str, int] = {}

    if root_folder_id:
        existing = (
            await session.execute(select(Folder).where(Folder.id == root_folder_id))
        ).scalar_one_or_none()
        if existing:
            mapping[""] = existing.id
        else:
            root_folder_id = None

    if not root_folder_id:
        root_folder = Folder(
            name=folder_name,
            workspace_id=workspace_id,
            created_by_id=user_id,
            position="a0",
        )
        session.add(root_folder)
        await session.flush()
        mapping[""] = root_folder.id
        root_folder_id = root_folder.id

    for rel_dir in subdirs:
        dir_parts = Path(rel_dir).parts
        dir_name = dir_parts[-1]
        parent_rel = str(Path(*dir_parts[:-1])) if len(dir_parts) > 1 else ""

        parent_id = mapping.get(parent_rel, mapping[""])

        existing_folder = (
            await session.execute(
                select(Folder).where(
                    Folder.name == dir_name,
                    Folder.parent_id == parent_id,
                    Folder.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()

        if existing_folder:
            mapping[rel_dir] = existing_folder.id
        else:
            new_folder = Folder(
                name=dir_name,
                parent_id=parent_id,
                workspace_id=workspace_id,
                created_by_id=user_id,
                position="a0",
            )
            session.add(new_folder)
            await session.flush()
            mapping[rel_dir] = new_folder.id

    await session.flush()
    return mapping, root_folder_id


async def index_uploaded_files(
    session: AsyncSession,
    workspace_id: int,
    user_id: str,
    folder_name: str,
    root_folder_id: int,
    file_mappings: list[dict],
    on_heartbeat_callback: HeartbeatCallbackType | None = None,
    use_vision_llm: bool = False,
    processing_mode: str = "basic",
) -> tuple[int, int, str | None]:
    """Index files uploaded by the browser from a folder picker.

    Each entry in *file_mappings* is ``{temp_path, relative_path, filename}``.
    This function mirrors the folder structure from the provided relative
    paths, then indexes each file from its temp path. Temp files are cleaned
    up after processing.

    Returns ``(indexed_count, failed_count, error_summary_or_none)``.
    """
    from app.etl_pipeline.etl_document import ProcessingMode

    mode = ProcessingMode.coerce(processing_mode)

    task_logger = TaskLoggingService(session, workspace_id)
    log_entry = await task_logger.log_task_start(
        task_name="local_folder_indexing",
        source="uploaded_folder_indexing",
        message=f"Indexing {len(file_mappings)} uploaded file(s) for {folder_name}",
        metadata={"file_count": len(file_mappings), "processing_mode": mode.value},
    )

    try:
        all_relative_paths = [m["relative_path"] for m in file_mappings]
        _folder_mapping, root_folder_id = await _mirror_folder_structure_from_paths(
            session=session,
            relative_paths=all_relative_paths,
            folder_name=folder_name,
            workspace_id=workspace_id,
            user_id=user_id,
            root_folder_id=root_folder_id,
        )
        await session.flush()

        await _set_indexing_flag(session, root_folder_id)

        pipeline = IndexingPipelineService(session)

        vision_llm_instance = None
        if use_vision_llm:
            from app.services.llm_service import get_vision_llm

            vision_llm_instance = await get_vision_llm(session, workspace_id)

        indexed_count = 0
        failed_count = 0
        errors: list[str] = []

        for i, mapping in enumerate(file_mappings):
            temp_path = mapping["temp_path"]
            relative_path = mapping["relative_path"]
            filename = mapping["filename"]

            try:
                unique_id = f"{folder_name}:{relative_path}"
                uid_hash = compute_identifier_hash(
                    DocumentType.LOCAL_FOLDER_FILE.value,
                    unique_id,
                    workspace_id,
                )

                raw_hash = await asyncio.to_thread(_compute_raw_file_hash, temp_path)

                existing = await check_document_by_unique_identifier(session, uid_hash)

                if existing:
                    stored_raw_hash = (existing.document_metadata or {}).get(
                        "raw_file_hash"
                    )
                    if stored_raw_hash and stored_raw_hash == raw_hash:
                        meta = dict(existing.document_metadata or {})
                        meta["mtime"] = datetime.now(UTC).timestamp()
                        existing.document_metadata = meta
                        if not DocumentStatus.is_state(
                            existing.status, DocumentStatus.READY
                        ):
                            existing.status = DocumentStatus.ready()
                        await session.commit()
                        continue

                try:
                    content, content_hash = await _compute_file_content_hash(
                        temp_path,
                        filename,
                        workspace_id,
                        vision_llm=vision_llm_instance,
                        processing_mode=mode.value,
                    )
                except Exception as e:
                    logger.warning(f"Could not read {relative_path}: {e}")
                    failed_count += 1
                    errors.append(f"{filename}: {e}")
                    continue

                if not content.strip():
                    failed_count += 1
                    continue

                if existing:
                    if existing.content_hash == content_hash:
                        meta = dict(existing.document_metadata or {})
                        meta["mtime"] = datetime.now(UTC).timestamp()
                        meta["raw_file_hash"] = raw_hash
                        existing.document_metadata = meta
                        if not DocumentStatus.is_state(
                            existing.status, DocumentStatus.READY
                        ):
                            existing.status = DocumentStatus.ready()
                        await session.commit()
                        continue

                    await create_version_snapshot(session, existing)

                connector_doc = _build_connector_doc(
                    title=filename,
                    content=content,
                    relative_path=relative_path,
                    folder_name=folder_name,
                    workspace_id=workspace_id,
                    user_id=user_id,
                )

                connector_doc.folder_id = await _resolve_folder_for_file(
                    session,
                    relative_path,
                    root_folder_id,
                    workspace_id,
                    user_id,
                )

                documents = await pipeline.prepare_for_indexing([connector_doc])
                if not documents:
                    failed_count += 1
                    continue

                db_doc = documents[0]

                result = await pipeline.index_unless_store_owns(db_doc, connector_doc)

                await session.refresh(db_doc)
                doc_meta = dict(db_doc.document_metadata or {})
                doc_meta["mtime"] = datetime.now(UTC).timestamp()
                doc_meta["raw_file_hash"] = raw_hash
                db_doc.document_metadata = doc_meta
                await session.commit()

                # A deferral (``None``) is a success: the store's indexer owns
                # its chunks.
                if result is None or DocumentStatus.is_state(
                    db_doc.status, DocumentStatus.READY
                ):
                    indexed_count += 1
                else:
                    failed_count += 1

                if on_heartbeat_callback and (i + 1) % 5 == 0:
                    await on_heartbeat_callback(i + 1)

            except Exception as e:
                logger.exception(f"Error indexing uploaded file {relative_path}: {e}")
                await session.rollback()
                failed_count += 1
                errors.append(f"{filename}: {e}")
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(temp_path)

        error_summary = None
        if errors:
            error_summary = f"{failed_count} file(s) failed: " + "; ".join(errors[:5])
            if len(errors) > 5:
                error_summary += f" ... and {len(errors) - 5} more"

        await task_logger.log_task_success(
            log_entry,
            f"Upload indexing complete: {indexed_count} indexed, {failed_count} failed",
            {"indexed": indexed_count, "failed": failed_count},
        )

        return indexed_count, failed_count, error_summary

    except SQLAlchemyError as e:
        logger.exception(f"Database error during uploaded file indexing: {e}")
        await session.rollback()
        await task_logger.log_task_failure(
            log_entry, f"DB error: {e}", "Database error", {}
        )
        return 0, 0, f"Database error: {e}"

    except Exception as e:
        logger.exception(f"Error during uploaded file indexing: {e}")
        await task_logger.log_task_failure(
            log_entry, f"Error: {e}", "Unexpected error", {}
        )
        return 0, 0, str(e)

    finally:
        await _clear_indexing_flag(session, root_folder_id)
