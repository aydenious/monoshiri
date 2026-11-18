"""Unit tests for the MinerU 4.x async task API client (httpx.MockTransport replays)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.conversion import mineru_client
from app.conversion.mineru_client import (
    MinerUClient,
    MinerUError,
    MinerUTaskFailed,
    MinerUTaskNotFound,
    MinerUUnavailable,
    strip_zip_image_links,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "mineru"
BASE = "http://mineru-api:8000"
TASK_ID = "job_725ae477379fced42b5e0395"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _client(handler) -> MinerUClient:
    return MinerUClient(BASE, "k", transport=httpx.MockTransport(handler))


def _zip_bytes(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, data)
    return buf.getvalue()


async def test_submit_creates_then_uploads(tmp_path):
    source = tmp_path / "fracture.pdf"
    payload = b"%PDF-1.4 example bytes"
    source.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    create = _fixture("ok/create_task.json")
    upload = _fixture("ok/upload.json")
    upload_url = create["body"]["files"][0]["upload"]["url"]
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["post_url"] = str(request.url)
            seen["post_auth"] = request.headers.get("authorization")
            seen["post_body"] = json.loads(await request.aread())
            return httpx.Response(
                create["status"],
                json=create["body"],
                headers={"content-type": create["content_type"]},
            )
        assert request.method == "PUT"
        seen["put_url"] = str(request.url)
        seen["put_type"] = request.headers.get("content-type")
        seen["put_auth"] = request.headers.get("authorization")
        seen["put_body"] = await request.aread()
        return httpx.Response(
            upload["status"],
            json=upload["body"],
            headers={"content-type": upload["content_type"]},
        )

    task_id = await _client(handler).submit(source, "fracture.pdf", page_range="11-20")

    assert task_id == TASK_ID
    assert seen["post_url"] == f"{BASE}/v1/tasks"
    assert seen["post_auth"] == "Bearer k"
    body = seen["post_body"]
    assert body["tier"] == "standard"
    assert body["page_range"] == "11-20"
    assert body["files"][0]["file_name"] == "fracture.pdf"
    assert body["files"][0]["bytes"] == len(payload)
    assert body["files"][0]["sha256sum"] == digest

    assert seen["put_url"] == upload_url
    assert seen["put_type"] == "application/octet-stream"
    assert seen["put_auth"] == "Bearer k"
    assert seen["put_body"] == payload


async def test_submit_skips_upload_when_server_already_has_file(tmp_path):
    """MinerU dedups by sha256: a later batch comes back pending, no upload."""
    source = tmp_path / "p.pdf"
    source.write_bytes(b"%PDF-1.4 same bytes")
    create = {
        "task_id": "job_2f400a799eb44abe39e1aad2",
        "status": "pending",
        "files": [
            {
                "file_id": "file-b01c73d326adc03e0b786f4f",
                "file_name": "p.pdf",
                "status": "pending",
            }
        ],
    }
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(202, json=create)

    task_id = await _client(handler).submit(source, "p.pdf", page_range="11-20")

    assert task_id == "job_2f400a799eb44abe39e1aad2"
    assert calls == ["POST"]  # no PUT: the server already has the bytes


async def test_submit_uses_supplied_facts_without_rehashing(tmp_path, monkeypatch):
    """A multi-batch upload must not re-hash the source for every batch."""
    source = tmp_path / "p.pdf"
    source.write_bytes(b"%PDF-1.4 stashed facts")
    create = {
        "task_id": "job_2f400a799eb44abe39e1aad2",
        "status": "pending",
        "files": [
            {
                "file_id": "file-b01c73d326adc03e0b786f4f",
                "file_name": "p.pdf",
                "status": "pending",
            }
        ],
    }
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(await request.aread())
        return httpx.Response(202, json=create)

    def must_not_hash(_path):
        raise AssertionError("the source was re-hashed for a later batch")

    monkeypatch.setattr(mineru_client, "_file_facts", must_not_hash)

    await _client(handler).submit(
        source, "p.pdf", page_range="1-10", size=17, sha256="a" * 64
    )

    assert seen["body"]["files"][0]["bytes"] == 17
    assert seen["body"]["files"][0]["sha256sum"] == "a" * 64


async def test_submit_without_upload_while_waiting_for_file_is_invalid(tmp_path):
    """A waiting-file task with no upload URL is unusable, as before."""
    source = tmp_path / "p.pdf"
    source.write_bytes(b"%PDF-1.4 same bytes")
    create = {
        "task_id": "job_3125821ad17c1219b6749cc6",
        "status": "waiting-file",
        "files": [
            {
                "file_id": "file-ea4dc6949efba6cd17c87d91",
                "file_name": "p.pdf",
                "status": "waiting-file",
            }
        ],
    }
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(202, json=create)

    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).submit(source, "p.pdf", page_range=None)

    assert excinfo.value.code == "invalid_response"
    assert "no upload URL" in excinfo.value.message
    assert calls == ["POST"]


async def test_submit_without_files_is_invalid(tmp_path):
    source = tmp_path / "p.pdf"
    source.write_bytes(b"%PDF-1.4")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"task_id": TASK_ID, "status": "pending"})

    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).submit(source, "p.pdf", page_range=None)
    assert excinfo.value.code == "invalid_response"


async def test_get_running_and_completed():
    running = _fixture("ok/get_task_running.json")
    completed = _fixture("ok/get_task_completed.json")

    async def running_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{BASE}/v1/tasks/{TASK_ID}"
        return httpx.Response(running["status"], json=running["body"])

    state = await _client(running_handler).get(TASK_ID)
    assert state.task_id == TASK_ID
    assert state.status == "running"
    assert state.result_url is None
    assert state.error is None

    async def completed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(completed["status"], json=completed["body"])

    state = await _client(completed_handler).get(TASK_ID)
    assert state.status == "completed"
    assert state.result_url is not None
    assert state.result_url.endswith("/v1/files/file-3e18cc1d0d3366e60d94436f/content")


async def test_get_failed_raises_task_failed():
    failed = _fixture("failed/get_task_failed.json")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(failed["status"], json=failed["body"])

    with pytest.raises(MinerUTaskFailed) as excinfo:
        await _client(handler).get("job_1c9a289590bc554c8fafc8b1")
    assert excinfo.value.message == "Unsupported file type: unknown"


async def test_get_unknown_raises_not_found():
    unknown = _fixture("ok/get_task_unknown.json")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(unknown["status"], json=unknown["body"])

    with pytest.raises(MinerUTaskNotFound) as excinfo:
        await _client(handler).get("00000000-0000-0000-0000-000000000000")
    assert excinfo.value.code == "task_not_found"


async def test_connect_error_and_5xx_and_401_are_unavailable():
    async def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    async def unauthorised(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "unauthorized"}})

    for handler in (connect_error, server_error, unauthorised):
        with pytest.raises(MinerUUnavailable):
            await _client(handler).get(TASK_ID)


async def test_fetch_markdown_reads_zip_and_strips_local_images():
    result_zip = (FIXTURES / "ok" / "result.zip").read_bytes()
    expected = (FIXTURES / "ok" / "markdown.md").read_text()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=result_zip)

    result_url = f"{BASE}/v1/files/file-3e18cc1d0d3366e60d94436f/content"
    markdown = await _client(handler).fetch_markdown(result_url)
    assert markdown == strip_zip_image_links(expected)

    mixed = (
        "before\n![](images/x.jpg)\n"
        "![alt](<images/y z.png>)\n"
        "![](https://e.com/a.png)\nafter\n"
    )
    stripped = strip_zip_image_links(mixed)
    assert "images/x.jpg" not in stripped
    assert "images/y z.png" not in stripped
    assert "![](https://e.com/a.png)" in stripped
    assert stripped.startswith("before")
    assert stripped.endswith("after\n")


async def test_foreign_result_url_refused():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made to a foreign origin")

    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).fetch_markdown("http://evil:8000/v1/files/x/content")
    assert excinfo.value.code == "foreign_url"


async def test_zip_bomb_refused():
    bomb = _zip_bytes("markdown.md", b"\0" * (50 * 1024 * 1024 + 1))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bomb)

    result_url = f"{BASE}/v1/files/file-3e18cc1d0d3366e60d94436f/content"
    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).fetch_markdown(result_url)
    assert excinfo.value.code == "result_too_large"

    assert mineru_client.MAX_MARKDOWN_BYTES == 50 * 1024 * 1024


async def test_3xx_is_an_error_and_nothing_is_uploaded(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4")
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(307, headers={"location": "http://elsewhere:9/v1"})

    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).submit(source, "book.pdf", page_range=None)
    assert excinfo.value.code == "redirect"
    assert calls == ["POST"]


async def test_2xx_non_json_or_non_object_body_is_invalid_response(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4")

    async def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy says hi</html>")

    with pytest.raises(MinerUError) as excinfo:
        await _client(html).submit(source, "book.pdf", page_range=None)
    assert excinfo.value.code == "invalid_response"
    assert "not JSON" in excinfo.value.message

    with pytest.raises(MinerUError) as excinfo:
        await _client(html).get(TASK_ID)
    assert excinfo.value.code == "invalid_response"

    async def array(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    with pytest.raises(MinerUError) as excinfo:
        await _client(array).get(TASK_ID)
    assert excinfo.value.code == "invalid_response"
    assert "not a JSON object" in excinfo.value.message


async def test_408_and_429_are_unavailable():
    async def timeout(request: httpx.Request) -> httpx.Response:
        return httpx.Response(408, text="request timeout")

    async def busy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"code": "rate_limited", "message": "converter busy"}},
        )

    for handler in (timeout, busy):
        with pytest.raises(MinerUUnavailable):
            await _client(handler).get(TASK_ID)


async def test_422_maps_to_a_plain_mineru_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "error": {
                    "code": "range_invalid",
                    "message": "page range outside the document",
                }
            },
        )

    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).get(TASK_ID)
    assert not isinstance(excinfo.value, MinerUUnavailable)
    assert excinfo.value.code == "range_invalid"
    assert excinfo.value.message == "page range outside the document"


async def test_read_timeout_is_unavailable():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(MinerUUnavailable):
        await _client(handler).get(TASK_ID)


async def test_put_5xx_is_unavailable(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4")
    create = _fixture("ok/create_task.json")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(create["status"], json=create["body"])
        await request.aread()
        return httpx.Response(503, text="storage down")

    with pytest.raises(MinerUUnavailable):
        await _client(handler).submit(source, "book.pdf", page_range=None)


async def test_foreign_upload_url_refused_without_leaking_the_url(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4")
    create = _fixture("ok/create_task.json")
    body = json.loads(json.dumps(create["body"]))
    body["files"][0]["upload"]["url"] = (
        "http://evil.example:8000/upload?sig=SECRET-TOKEN"
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=body)

    with pytest.raises(MinerUError) as excinfo:
        await _client(handler).submit(source, "book.pdf", page_range=None)
    assert excinfo.value.code == "foreign_url"
    assert calls == ["POST"]
    assert "SECRET-TOKEN" not in str(excinfo.value)
    assert "sig=" not in str(excinfo.value)
    assert "evil.example" in str(excinfo.value)


async def test_cancel_swallows_errors():
    async def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    await _client(server_error).cancel(TASK_ID)
    await _client(connect_error).cancel(TASK_ID)


async def test_api_key_never_appears_in_error_text(tmp_path):
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.4")
    secret = "super-secret-api-key"
    client = MinerUClient(
        BASE,
        secret,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, text="upstream down")
        ),
    )
    with pytest.raises(MinerUUnavailable) as excinfo:
        await client.get(TASK_ID)
    assert secret not in str(excinfo.value)
    assert secret not in repr(excinfo.value)
    assert "Bearer" not in str(excinfo.value)

    async def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = MinerUClient(BASE, secret, transport=httpx.MockTransport(connect_error))
    with pytest.raises(MinerUUnavailable) as excinfo:
        await client.submit(source, "book.pdf", page_range=None)
    assert secret not in str(excinfo.value)
    assert "Bearer" not in str(excinfo.value)


async def test_cancel_while_mineru_is_down_is_a_warning(caplog):
    async def connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    await _client(connect_error).cancel(TASK_ID)

    cancel = [r for r in caplog.records if "cancel" in r.getMessage()]
    assert cancel and all(r.levelname == "WARNING" and not r.exc_info for r in cancel)
