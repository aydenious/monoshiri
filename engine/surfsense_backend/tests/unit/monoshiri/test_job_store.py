"""Durable per-document conversion job store (files on a mounted volume)."""

from __future__ import annotations

import json
import os
import time

import pytest

from app.conversion.job_store import (
    ConversionCapacityError,
    CorruptJobError,
    Job,
    JobGone,
    JobStore,
)

pytestmark = pytest.mark.unit


def _job(**overrides) -> Job:
    data = {
        "document_id": 1,
        "filename": "book.pdf",
        "workspace_id": 1,
        "user_id": "user-1",
        "use_vision_llm": False,
        "processing_mode": "basic",
        "source_name": "source.pdf",
        "source_sha256": "0" * 64,
        "pages_total": 400,
        "batch_pages": 10,
    }
    data.update(overrides)
    return Job(**data)


def _source(tmp_path, size: int = 64):
    path = tmp_path / "upload.bin"
    path.write_bytes(b"x" * size)
    return path


def _store(tmp_path, max_bytes: int = 1024**3) -> JobStore:
    return JobStore(tmp_path / "jobs", max_bytes)


def test_create_load_roundtrip(tmp_path):
    store = _store(tmp_path)
    job = _job()
    source = _source(tmp_path)

    store.create(job, source)
    loaded = store.load(job.document_id)

    assert loaded == job
    assert store.source_path(job).read_bytes() == source.read_bytes()
    assert store.source_path(job).name == "source.pdf"


def test_batch_ranges_pdf_400_pages_10_per_batch():
    job = _job(pages_total=400, batch_pages=10)
    ranges = job.batch_ranges()

    assert len(ranges) == 40
    assert ranges[0] == "1-10"
    assert ranges[-1] == "391-400"

    job.next_batch = 0
    assert job.pages_done() == 0
    job.next_batch = 14
    assert job.pages_done() == 140
    job.next_batch = 40
    assert job.pages_done() == 400


def test_batch_ranges_partial_last():
    assert _job(pages_total=25, batch_pages=10).batch_ranges() == [
        "1-10",
        "11-20",
        "21-25",
    ]


def test_batch_ranges_non_pdf():
    job = _job(filename="photo.heic", pages_total=None)
    assert job.batch_ranges() == [None]
    assert job.pages_done() == 0


def test_save_is_atomic(tmp_path, monkeypatch):
    import app.conversion.job_store as job_store_module

    store = _store(tmp_path)
    job = _job()
    store.create(job, _source(tmp_path))

    job.next_batch = 1
    store.save(job)
    assert store.load(job.document_id).next_batch == 1

    job.next_batch = 2

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(job_store_module.os, "replace", boom)
    with pytest.raises(OSError):
        store.save(job)

    reloaded = store.load(job.document_id)
    assert reloaded is not None
    assert reloaded.next_batch == 1


def test_merged_markdown_orders_batches(tmp_path):
    store = _store(tmp_path)
    job = _job(pages_total=30)
    store.create(job, _source(tmp_path))

    store.write_batch(job, 2, "three")
    store.write_batch(job, 0, "one")
    store.write_batch(job, 1, "two")

    assert store.merged_markdown(job) == "one\n\ntwo\n\nthree"
    assert (store._dir(job.document_id) / "batch-0001.md").read_text() == "one"


def test_create_replaces_existing_job_dir(tmp_path):
    store = _store(tmp_path)
    source = _source(tmp_path)
    job = _job(pages_total=30, next_batch=3)
    store.create(job, source)
    store.write_batch(job, 0, "old")

    job.next_batch = 0
    store.create(job, source)

    loaded = store.load(job.document_id)
    assert loaded is not None
    assert loaded.next_batch == 0
    assert not list(store._dir(job.document_id).glob("batch-*.md"))
    assert store.merged_markdown(job) == ""


def test_capacity_refused(tmp_path):
    source = _source(tmp_path, size=100)
    store = _store(tmp_path, max_bytes=50)

    with pytest.raises(ConversionCapacityError):
        store.create(_job(), source)
    assert not (tmp_path / "jobs" / "1").exists()

    # Also counts space used by other documents' jobs.
    store = _store(tmp_path, max_bytes=150)
    store.create(_job(document_id=1), source)
    with pytest.raises(ConversionCapacityError):
        store.create(_job(document_id=2), _source(tmp_path, size=100))


def test_hostile_filename_never_used_as_path(tmp_path):
    store = _store(tmp_path)
    job = _job(filename="../../etc/passwd.pdf", source_name="source.pdf")
    source = _source(tmp_path)

    store.create(job, source)

    expected = tmp_path / "jobs" / "1" / "source.pdf"
    assert store.source_path(job) == expected
    assert expected.read_bytes() == source.read_bytes()
    assert not (tmp_path.parent / "etc" / "passwd.pdf").exists()
    assert not (tmp_path / "jobs" / "1" / "passwd.pdf").exists()


def test_list_ids_ignores_non_numeric(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    for name in ("1", "7", "999"):
        (root / name).mkdir()
    (root / "not-a-number").mkdir()
    (root / "3.txt").write_text("x")
    store = JobStore(root, 1024**3)

    assert store.list_ids() == [1, 7, 999]


def test_list_ids_empty_root(tmp_path):
    store = JobStore(tmp_path / "missing", 1024**3)
    assert store.list_ids() == []


def test_job_json_is_plain_json(tmp_path):
    store = _store(tmp_path)
    job = _job()
    store.create(job, _source(tmp_path))

    data = json.loads((store._dir(job.document_id) / "job.json").read_text())
    assert data["document_id"] == 1
    assert data["pages_total"] == 400


# ---------------------------------------------------------------------------
# Corrupt / missing / gone state (T5 review)
# ---------------------------------------------------------------------------


def test_missing_job_json_returns_none(tmp_path):
    store = _store(tmp_path)
    (tmp_path / "jobs" / "9").mkdir(parents=True)
    assert store.load(9) is None


def test_load_corrupt_json_raises_corrupt_job_error(tmp_path):
    store = _store(tmp_path)
    directory = tmp_path / "jobs" / "1"
    directory.mkdir(parents=True)
    (directory / "job.json").write_text("{this is not json")

    with pytest.raises(CorruptJobError):
        store.load(1)


def test_load_json_array_raises_corrupt_job_error(tmp_path):
    store = _store(tmp_path)
    directory = tmp_path / "jobs" / "1"
    directory.mkdir(parents=True)
    (directory / "job.json").write_text("[1, 2, 3]")

    with pytest.raises(CorruptJobError):
        store.load(1)


def test_load_unknown_key_raises_corrupt_job_error(tmp_path):
    store = _store(tmp_path)
    store.create(_job(), _source(tmp_path))
    path = store._dir(1) / "job.json"
    data = json.loads(path.read_text())
    data["unexpected_key"] = "boom"
    path.write_text(json.dumps(data))

    with pytest.raises(CorruptJobError):
        store.load(1)


def test_load_invalid_batch_pages_raises_corrupt_job_error(tmp_path):
    store = _store(tmp_path)
    store.create(_job(), _source(tmp_path))
    path = store._dir(1) / "job.json"
    data = json.loads(path.read_text())
    data["batch_pages"] = 0
    path.write_text(json.dumps(data))

    with pytest.raises(CorruptJobError):
        store.load(1)


def test_save_and_write_batch_after_delete_raise_job_gone(tmp_path):
    store = _store(tmp_path)
    job = _job()
    store.create(job, _source(tmp_path))
    store.delete(job.document_id)

    with pytest.raises(JobGone):
        store.save(job)
    with pytest.raises(JobGone):
        store.write_batch(job, 0, "x")
    # A failed save must not resurrect the directory.
    assert not (tmp_path / "jobs" / "1").exists()


def test_write_batch_rejects_negative_index(tmp_path):
    store = _store(tmp_path)
    job = _job()
    store.create(job, _source(tmp_path))

    with pytest.raises(ValueError):
        store.write_batch(job, -1, "x")


def test_batch_pages_must_be_positive():
    with pytest.raises(ValueError):
        _job(batch_pages=0)
    with pytest.raises(ValueError):
        _job(batch_pages=-5)


def test_merged_markdown_rejects_gaps(tmp_path):
    store = _store(tmp_path)
    job = _job(pages_total=30)
    store.create(job, _source(tmp_path))
    store.write_batch(job, 0, "one")
    store.write_batch(job, 2, "three")

    with pytest.raises(CorruptJobError):
        store.merged_markdown(job)


def test_merged_markdown_expect_complete_requires_every_batch(tmp_path):
    store = _store(tmp_path)
    job = _job(pages_total=20)
    store.create(job, _source(tmp_path))

    with pytest.raises(CorruptJobError):
        store.merged_markdown(job, expect_complete=True)

    store.write_batch(job, 0, "one")
    with pytest.raises(CorruptJobError):
        store.merged_markdown(job, expect_complete=True)

    store.write_batch(job, 1, "two")
    assert store.merged_markdown(job, expect_complete=True) == "one\n\ntwo"


def test_failed_create_keeps_the_old_job_and_leaves_no_half_dir(tmp_path, monkeypatch):
    import app.conversion.job_store as job_store_module

    store = _store(tmp_path)
    old_source = _source(tmp_path, size=32)
    store.create(_job(filename="first.pdf"), old_source)
    store.write_batch(_job(), 0, "old batch")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(job_store_module.shutil, "copyfile", boom)
    with pytest.raises(OSError):
        store.create(_job(filename="second.pdf"), _source(tmp_path, size=32))

    assert store.load(1).filename == "first.pdf"
    assert store.merged_markdown(_job()) == "old batch"
    assert not (tmp_path / "jobs" / "1.new").exists()


def test_stale_staging_dirs_are_removed(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    stale = root / "2.new"
    fresh = root / "3.new"
    stale.mkdir()
    fresh.mkdir()
    old = time.time() - 3600
    os.utime(stale, (old, old))

    store = JobStore(root, 1024**3)
    store.remove_staging(older_than=300)

    assert not stale.exists()
    assert fresh.exists()
    assert store.list_ids() == []


def test_leftover_tmp_files_are_ignored(tmp_path):
    store = _store(tmp_path)
    job = _job()
    store.create(job, _source(tmp_path))
    directory = store._dir(job.document_id)
    (directory / ".job.json.dead.tmp").write_text("{partial")
    (directory / ".batch-0002.md.dead.tmp").write_text("half a batch")

    assert store.load(1) == job
    store.write_batch(job, 0, "one")
    assert store.merged_markdown(job) == "one"
