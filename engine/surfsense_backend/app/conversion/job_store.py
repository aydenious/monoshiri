"""Durable per-document conversion jobs on a mounted volume.

A conversion must survive a worker restart, a host hibernation or a SIGKILL:
every finished page batch and the resumable job state live under
``{CONVERSION_WORK_DIR}/{document_id}/`` on disk (never Redis, never the
status JSONB). ``job.json`` records which batch comes next; ``batch-NNNN.md``
is written atomically after each finished MinerU task.

Only :meth:`JobStore.create` may create the job directory, and it builds the
replacement in a staging directory first: a crash can leave a harmless staging
directory (removed by the resume sweep) but never a half-written job, and a
failed copy never destroys the previous job.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath


class ConversionCapacityError(Exception):
    """The conversion work directory cannot hold another source file."""


class CorruptJobError(Exception):
    """Stored job state exists but cannot be trusted (bad JSON or fields)."""


class JobGone(Exception):  # noqa: N818 - defined by the T5 review contract
    """The job directory vanished (document deleted or job replaced/removed)."""


@dataclass
class Job:
    document_id: int
    filename: str
    workspace_id: int
    user_id: str
    use_vision_llm: bool
    processing_mode: str
    source_name: str  # fixed name ("source.pdf"), never the user's filename
    source_sha256: str
    pages_total: int | None  # None for non-PDF inputs
    batch_pages: int
    next_batch: int = 0  # index of the first unfinished batch
    mineru_task_id: str | None = None
    # Size of the stored source, so batch submits never re-hash it. Older job
    # files lack it; the client computes it once in that case.
    source_size: int | None = None
    # When the current batch's MinerU task was submitted (epoch seconds) and
    # how often this batch has already been re-submitted after a timeout.
    batch_submitted_at: float | None = None
    batch_resubmits: int = 0
    unreachable_since: float | None = None  # epoch seconds
    attempts: int = 0
    step_errors: int = 0  # consecutive unexpected step failures
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        # batch_ranges() would never terminate with a non-positive size.
        if int(self.batch_pages) <= 0:
            raise ValueError(f"batch_pages must be positive, got {self.batch_pages!r}")

    def batch_ranges(self) -> list[str | None]:
        """MinerU ``page_range`` value per batch, in order."""
        if self.pages_total is None:
            return [None]
        ranges: list[str | None] = []
        start = 1
        while start <= self.pages_total:
            end = min(start + self.batch_pages - 1, self.pages_total)
            ranges.append(f"{start}-{end}")
            start = end + 1
        return ranges

    def pages_done(self) -> int:
        if self.pages_total is None:
            return 0
        return min(self.next_batch * self.batch_pages, self.pages_total)


def _tree_size(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def _fsync_dir(path: Path) -> None:
    """Persist a directory entry (best effort; not every FS supports it)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    """Atomically write *data*; never creates the parent directory.

    A missing directory means the job was deleted (or replaced) while a step
    was in flight, so the caller gets :class:`JobGone` instead of silently
    resurrecting it.
    """
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
    except FileNotFoundError as exc:
        raise JobGone(f"conversion job directory is gone: {path.parent}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)


def _job_bytes(job: Job) -> bytes:
    return json.dumps(asdict(job), indent=2, sort_keys=True).encode("utf-8")


class JobStore:
    """File-backed store; one directory per document id under *root*."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes

    # -- paths ------------------------------------------------------------
    def _dir(self, document_id: int) -> Path:
        return self._root / str(int(document_id))

    def _staging_dir(self, document_id: int) -> Path:
        # Non-numeric name: list_ids() never mistakes it for a document.
        return self._root / f"{int(document_id)}.new"

    def _job_path(self, document_id: int) -> Path:
        return self._dir(document_id) / "job.json"

    @staticmethod
    def _source_name(job: Job) -> str:
        # ``source_name`` is the fixed name chosen at creation time (it becomes
        # ``source.png`` for a converted HEIC). Never the user's filename.
        return PurePosixPath(job.source_name).name

    def source_path(self, job: Job) -> Path:
        return self._dir(job.document_id) / self._source_name(job)

    # -- lifecycle --------------------------------------------------------
    def create(self, job: Job, source: Path) -> Job:
        """(Re)create the job directory from *source*, refusing when too full."""
        source = Path(source)
        target = self._dir(job.document_id)
        source_size = source.stat().st_size
        used = self.used_bytes() - _tree_size(target)
        if used + source_size > self._max_bytes:
            raise ConversionCapacityError(
                f"conversion work dir is full: {used} used + {source_size} new "
                f"> {self._max_bytes} bytes"
            )

        staging = self._staging_dir(job.document_id)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            shutil.copyfile(source, staging / self._source_name(job))
            _atomic_write(staging / "job.json", _job_bytes(job))
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # The previous job survives any failure above; only now is it dropped.
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        _fsync_dir(self._root)
        return job

    def load(self, document_id: int) -> Job | None:
        """Load the job, or ``None`` when there is no job for *document_id*.

        Unreadable or unparseable state raises :class:`CorruptJobError` so the
        caller can fail the document with a reason instead of crash-looping.
        """
        try:
            raw = self._job_path(document_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CorruptJobError(f"job.json is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise CorruptJobError("job.json is not a JSON object")
        try:
            return Job(**data)
        except (TypeError, ValueError) as exc:
            raise CorruptJobError(f"job.json is invalid: {exc}") from exc

    def save(self, job: Job) -> None:
        """Persist the job; raises :class:`JobGone` if the directory is gone."""
        _atomic_write(self._job_path(job.document_id), _job_bytes(job))

    def write_batch(self, job: Job, index: int, markdown: str) -> None:
        """Persist batch *index* (0-based) as ``batch-NNNN.md`` (1-based name)."""
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError(f"batch index must be a non-negative int, got {index!r}")
        path = self._dir(job.document_id) / f"batch-{index + 1:04d}.md"
        _atomic_write(path, markdown.encode("utf-8"))

    def merged_markdown(self, job: Job, *, expect_complete: bool = False) -> str:
        """Concatenate the batches in order.

        Missing indexes are corruption (batches are only ever written in
        order); with *expect_complete* the count must also match the job's
        page ranges, which is checked right before the final merge.
        """
        directory = self._dir(job.document_id)
        if not directory.is_dir():
            return ""
        batches = sorted(
            directory.glob("batch-*.md"),
            key=lambda path: int(path.stem.rsplit("-", 1)[1]),
        )
        if not batches:
            if expect_complete:
                raise CorruptJobError(
                    f"no batches stored for document {job.document_id}"
                )
            return ""
        indices = [int(path.stem.rsplit("-", 1)[1]) - 1 for path in batches]
        if indices != list(range(len(indices))):
            raise CorruptJobError(
                f"batch files are not contiguous: {indices} "
                f"for document {job.document_id}"
            )
        expected = len(job.batch_ranges())
        if expect_complete and len(indices) != expected:
            raise CorruptJobError(
                f"expected {expected} batches, found {len(indices)} "
                f"for document {job.document_id}"
            )
        return "\n\n".join(path.read_text(encoding="utf-8") for path in batches)

    def delete(self, document_id: int) -> None:
        shutil.rmtree(self._dir(document_id), ignore_errors=True)

    def delete_staging(self, document_id: int) -> None:
        """Drop this document's staging directory (a failed/killed create)."""
        shutil.rmtree(self._staging_dir(document_id), ignore_errors=True)

    def remove_staging(self, *, older_than: float = 300.0) -> None:
        """Drop staging directories left behind by a killed :meth:`create`."""
        if not self._root.is_dir():
            return
        cutoff = time.time() - older_than
        for entry in self._root.iterdir():
            name = entry.name
            if not (entry.is_dir() and name.endswith(".new") and name[:-4].isdigit()):
                continue
            with contextlib.suppress(OSError):
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)

    def list_ids(self) -> list[int]:
        if not self._root.is_dir():
            return []
        return sorted(
            int(entry.name)
            for entry in self._root.iterdir()
            if entry.is_dir() and entry.name.isdigit()
        )

    def used_bytes(self) -> int:
        return _tree_size(self._root)


def job_store_from_config() -> JobStore:
    from app.config import config

    return JobStore(
        Path(config.CONVERSION_WORK_DIR), config.CONVERSION_WORK_DIR_MAX_BYTES
    )
