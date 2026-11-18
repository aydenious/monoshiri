"""Async HTTP client for the MinerU 4.x task API.

MinerU exposes a small async task API under ``/v1`` (no host port on the live
stack; the backend reaches it over the ``mineru-net`` Docker network):

* ``POST /v1/tasks`` — register a task and get per-file upload URLs;
* ``PUT <upload.url>`` — stream the file;
* ``GET /v1/tasks/{id}`` — poll (there is no per-page progress; the caller
  batches pages and treats one task as one progress step);
* ``GET <result_url>`` — a zip with ``markdown.md`` (plus images);
* ``POST /v1/tasks/{id}/cancel`` — best effort.

Every URL MinerU hands back must stay on the configured origin: the API is
deliberately loopback/internal and a malicious/compromised task response must
not turn this client into an SSRF gadget.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "http://mineru-api:8000"
MAX_MARKDOWN_BYTES = 50 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_IMAGE_LINK = re.compile(r"!\[[^\]]*\]\(\s*<?\s*images/[^)]*\)")


class MinerUError(Exception):
    """A MinerU request failed. ``code`` is stable, ``message`` is human text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MinerUUnavailable(MinerUError):  # noqa: N818
    """MinerU is down, unreachable, slow, or rejecting our credentials."""

    def __init__(self, code: str = "unavailable", message: str = "MinerU unavailable"):
        super().__init__(code, message)


class MinerUTaskNotFound(MinerUError):  # noqa: N818
    """The task id is unknown (e.g. mineru-api restarted and lost its uploads)."""


class MinerUTaskFailed(MinerUError):  # noqa: N818
    """MinerU finished the task but the file failed or was cancelled."""


@dataclass(frozen=True)
class TaskState:
    task_id: str
    status: str  # waiting-file|pending|running|completed|partial|failed|canceled
    result_url: str | None = None
    error: str | None = None


def _file_facts(path: Path) -> tuple[int, str]:
    """Size + sha256 of *path*; blocking, call via ``asyncio.to_thread``."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    with path.open("rb") as fh:
        while chunk := fh.read(_UPLOAD_CHUNK_BYTES):
            yield chunk


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return (
        scheme,
        (parts.hostname or "").lower(),
        parts.port or _DEFAULT_PORTS.get(scheme),
    )


def _origin_label(origin: tuple[str, str, int | None]) -> str:
    scheme, host, port = origin
    return f"{scheme}://{host}:{port}"


def strip_zip_image_links(markdown: str) -> str:
    """Drop ``![](images/...)`` references (the zip's images are not stored)."""
    return _LOCAL_IMAGE_LINK.sub("", markdown)


class MinerUClient:
    """Minimal client for the MinerU async task API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        tier: str = "standard",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._tier = tier
        self._timeout = timeout
        self._transport = transport
        self._origin = _origin(self._base)

    # -- plumbing ---------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _ensure_same_origin(self, url: str, what: str) -> None:
        # Never echo the URL itself: MinerU's signed URLs carry credentials.
        try:
            origin = _origin(url)
        except ValueError as exc:
            raise MinerUError(
                "foreign_url", f"MinerU returned a {what} with an invalid URL"
            ) from exc
        if origin != self._origin:
            raise MinerUError(
                "foreign_url",
                f"MinerU returned a {what} on a different origin "
                f"({_origin_label(origin)})",
            )

    async def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
            ) as client:
                return await client.request(method, url, headers=headers, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise MinerUUnavailable(
                "unavailable", f"MinerU request failed: {exc}"
            ) from exc

    @staticmethod
    def _error_from_response(response: httpx.Response) -> MinerUError:
        code = "http_error"
        message = f"MinerU returned HTTP {response.status_code}"
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                code = error.get("code") or code
                message = error.get("message") or message
        status = response.status_code
        if status == 404 and code == "task_not_found":
            return MinerUTaskNotFound(code, message)
        if status in (401, 403, 408, 429) or status >= 500:
            # Unreachable, overloaded, timed out or rejecting our credentials:
            # transient, so the document waits rather than failing.
            return MinerUUnavailable(code, message)
        if 300 <= status < 400:
            return MinerUError(
                "redirect",
                f"MinerU redirected (HTTP {status}); check MINERU_URL",
            )
        return MinerUError(code, message)

    @staticmethod
    def _json_object(response: httpx.Response, what: str) -> dict:
        """Parse a 2xx body as a JSON object; anything else is invalid."""
        try:
            data = response.json()
        except ValueError as exc:
            raise MinerUError(
                "invalid_response", f"MinerU {what} response is not JSON"
            ) from exc
        if not isinstance(data, dict):
            raise MinerUError(
                "invalid_response",
                f"MinerU {what} response is not a JSON object",
            )
        return data

    # -- API --------------------------------------------------------------
    async def submit(
        self,
        file_path: Path,
        file_name: str,
        *,
        page_range: str | None,
        size: int | None = None,
        sha256: str | None = None,
    ) -> str:
        """Create a MinerU task, upload the file if asked, return the id.

        MinerU dedups uploads by sha256. When it already holds these exact
        bytes — every page-range batch after the first sends the same file —
        the create-task response carries no ``upload`` block and the file is
        already ``pending``, so no PUT is needed and none is made.

        ``size``/``sha256`` may be supplied by a caller that already knows
        them (the job stores them at creation time) so a multi-batch upload
        does not re-hash the whole source for every batch.
        """
        path = Path(file_path)
        if size is None or sha256 is None:
            size, digest = await asyncio.to_thread(_file_facts, path)
        else:
            digest = sha256
        body: dict = {
            "tier": self._tier,
            "files": [
                {
                    "file_name": file_name,
                    "bytes": size,
                    "sha256sum": digest,
                }
            ],
        }
        if page_range is not None:
            body["page_range"] = page_range

        response = await self._send("POST", f"{self._base}/v1/tasks", json=body)
        if response.status_code >= 300:
            raise self._error_from_response(response)
        data = self._json_object(response, "create-task")
        task_id = data.get("task_id")
        files = data.get("files")
        entry = (
            files[0]
            if isinstance(files, list) and files and isinstance(files[0], dict)
            else None
        )
        if not isinstance(task_id, str) or not task_id or entry is None:
            raise MinerUError(
                "invalid_response", "MinerU create-task response has no upload URL"
            )

        upload = entry.get("upload")
        if upload is None:
            status = entry.get("status")
            if status == "waiting-file" or not isinstance(status, str) or not status:
                # The server is still waiting for bytes but gave us nowhere to
                # put them: the response is unusable.
                raise MinerUError(
                    "invalid_response", "MinerU create-task response has no upload URL"
                )
            # Dedup: MinerU already has the bytes (file "pending"), skip the PUT.
            return task_id

        upload_url = upload.get("url") if isinstance(upload, dict) else None
        if not isinstance(upload_url, str) or not upload_url:
            raise MinerUError(
                "invalid_response", "MinerU create-task response has no upload URL"
            )

        self._ensure_same_origin(upload_url, "upload URL")
        raw_headers = upload.get("headers") or {}
        if not isinstance(raw_headers, dict):
            raise MinerUError(
                "invalid_response", "MinerU upload headers are not an object"
            )
        upload_headers = dict(raw_headers)
        upload_headers.setdefault("Content-Type", "application/octet-stream")
        upload_headers["Content-Length"] = str(size)

        response = await self._send(
            "PUT",
            upload_url,
            content=_file_chunks(path),
            headers=upload_headers,
        )
        if response.status_code >= 300:
            raise self._error_from_response(response)
        return task_id

    async def get(self, task_id: str) -> TaskState:
        response = await self._send("GET", f"{self._base}/v1/tasks/{task_id}")
        if response.status_code >= 300:
            raise self._error_from_response(response)
        data = self._json_object(response, "task status")
        files = data.get("files")
        if not isinstance(files, list):
            files = []
        error = None
        result_url = None
        for item in files:
            item_error = item.get("error") if isinstance(item, dict) else None
            if error is None and isinstance(item_error, dict):
                error = item_error.get("message")
            if result_url is None and isinstance(item, dict):
                candidate = item.get("result_url")
                if isinstance(candidate, str) and candidate:
                    result_url = candidate
        status = data.get("status")
        if not isinstance(status, str) or not status:
            status = "unknown"
        if status in ("failed", "canceled"):
            code = "task_failed"
            for item in files:
                if isinstance(item, dict) and isinstance(item.get("error"), dict):
                    code = item["error"].get("code") or code
                    break
            raise MinerUTaskFailed(code, error or f"MinerU task {task_id} {status}")
        reported_id = data.get("task_id")
        return TaskState(
            task_id=reported_id
            if isinstance(reported_id, str) and reported_id
            else task_id,
            status=status,
            result_url=result_url,
            error=error,
        )

    async def fetch_markdown(self, result_url: str) -> str:
        self._ensure_same_origin(result_url, "result URL")
        response = await self._send("GET", result_url)
        if response.status_code >= 300:
            raise self._error_from_response(response)
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                try:
                    info = zf.getinfo("markdown.md")
                except KeyError as exc:
                    raise MinerUError(
                        "invalid_result", "MinerU result zip has no markdown.md"
                    ) from exc
                if info.file_size > MAX_MARKDOWN_BYTES:
                    raise MinerUError(
                        "result_too_large",
                        f"markdown.md is {info.file_size} bytes "
                        f"(limit {MAX_MARKDOWN_BYTES})",
                    )
                with zf.open(info) as fh:
                    raw = fh.read(MAX_MARKDOWN_BYTES + 1)
        except zipfile.BadZipFile as exc:
            raise MinerUError("invalid_result", "MinerU result is not a zip") from exc
        if len(raw) > MAX_MARKDOWN_BYTES:
            raise MinerUError(
                "result_too_large",
                f"markdown.md exceeds {MAX_MARKDOWN_BYTES} bytes",
            )
        return strip_zip_image_links(raw.decode("utf-8", errors="replace"))

    async def cancel(self, task_id: str) -> None:
        """Ask MinerU to cancel a task. Best effort: never raises."""
        try:
            response = await self._send(
                "POST", f"{self._base}/v1/tasks/{task_id}/cancel"
            )
            if response.status_code >= 400:
                logger.warning(
                    "MinerU cancel for %s returned HTTP %s",
                    task_id,
                    response.status_code,
                )
        except MinerUUnavailable:  # MinerU being down is a routine, tolerated state
            logger.warning("MinerU cancel for %s failed: MinerU unavailable", task_id)
        except Exception:  # cancellation is fire-and-forget
            logger.exception("MinerU cancel for %s failed", task_id)


def client_from_config() -> MinerUClient:
    """Build a client from ``config.MINERU_*`` settings."""
    from app.config import config

    # All three are defined by app.config; no silent fallbacks.
    return MinerUClient(
        config.MINERU_URL or BASE_URL,
        config.MINERU_API_KEY or "",
        tier=config.MINERU_TIER or "standard",
    )
