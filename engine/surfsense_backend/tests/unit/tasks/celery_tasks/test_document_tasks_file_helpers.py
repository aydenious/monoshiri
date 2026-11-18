"""``_file_size_or_none``/``_remove_temp_file`` handle only OS errors, and the
Redis heartbeats only redis errors."""

from __future__ import annotations

import logging

import pytest
import redis

from app.tasks.celery_tasks import document_tasks

pytestmark = pytest.mark.unit


def test_file_size_returns_the_size(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert document_tasks._file_size_or_none(str(f), "t") == 3


def test_file_size_of_missing_file_is_none_and_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=document_tasks.logger.name):
        assert document_tasks._file_size_or_none(str(tmp_path / "nope"), "t") is None
    assert "Could not get file size" in caplog.text


def test_file_size_does_not_swallow_non_os_errors():
    with pytest.raises(TypeError):
        document_tasks._file_size_or_none(None, "t")  # type: ignore[arg-type]


def test_remove_temp_file_deletes_it(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"x")
    document_tasks._remove_temp_file(str(f), "t")
    assert not f.exists()


def test_remove_temp_file_logs_a_failed_cleanup(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=document_tasks.logger.name):
        document_tasks._remove_temp_file(str(tmp_path / "nope"), "t")
    assert "Failed to clean up temp file" in caplog.text


class _Redis:
    """A redis client whose calls do whatever ``behaviour`` says."""

    def __init__(self, behaviour) -> None:
        self._behaviour = behaviour

    def setex(self, *_args) -> None:
        self._behaviour()

    def delete(self, *_args) -> None:
        self._behaviour()


def test_heartbeat_survives_a_redis_outage(monkeypatch, caplog):
    """A refused/timed-out redis call is a RedisError, never an OSError."""

    def refuse() -> None:
        raise redis.exceptions.ConnectionError("redis is down")

    monkeypatch.setattr(
        document_tasks, "_get_doc_heartbeat_redis", lambda: _Redis(refuse)
    )
    with caplog.at_level(logging.WARNING, logger=document_tasks.logger.name):
        document_tasks._start_heartbeat(7)
        document_tasks._stop_heartbeat(7)
    assert "Failed to set initial heartbeat for notification 7" in caplog.text
    assert "Heartbeat key delete failed" in caplog.text


def test_heartbeat_does_not_swallow_a_bug(monkeypatch):
    """Anything that is not a redis failure is ours to see, not to hide."""

    def boom() -> None:
        raise ValueError("bad key")

    monkeypatch.setattr(
        document_tasks, "_get_doc_heartbeat_redis", lambda: _Redis(boom)
    )
    with pytest.raises(ValueError):
        document_tasks._start_heartbeat(7)
