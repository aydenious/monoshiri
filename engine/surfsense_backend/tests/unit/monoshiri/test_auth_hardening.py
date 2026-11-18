import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.app as app_module
from app.config import config
from app.monoshiri.invites import create_invite
from app.rate_limiter import get_real_client_ip

pytestmark = pytest.mark.unit


def _req(headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234),
        }
    )


def test_client_ip_ignores_cf_and_real_ip_headers():
    req = _req({"cf-connecting-ip": "1.2.3.4", "x-real-ip": "5.6.7.8"})
    assert get_real_client_ip(req) == "10.0.0.9"


def test_client_ip_uses_rightmost_forwarded_entry():
    req = _req({"x-forwarded-for": "6.6.6.6, 203.0.113.7"})
    assert get_real_client_ip(req) == "203.0.113.7"


def _req_raw(headers: list[tuple[bytes, bytes]], client) -> Request:
    return Request({"type": "http", "headers": headers, "client": client})


@pytest.mark.parametrize(
    ("xff", "expected"),
    [
        ("", "10.0.0.9"),
        ("   ", "10.0.0.9"),
        ("1.2.3.4, ", "10.0.0.9"),
        ("1.2.3.4,", "10.0.0.9"),
        ("1.2.3.4", "1.2.3.4"),
        ("9.9.9.9, 8.8.8.8, 1.2.3.4", "1.2.3.4"),
    ],
)
def test_client_ip_xff_edge_cases(xff, expected):
    assert get_real_client_ip(_req({"x-forwarded-for": xff})) == expected


def test_client_ip_no_client_falls_back_to_loopback():
    assert get_real_client_ip(_req_raw([], None)) == "127.0.0.1"
    xff = [(b"x-forwarded-for", b"1.2.3.4")]
    assert get_real_client_ip(_req_raw(xff, None)) == "1.2.3.4"


def _route_calls(method: str, path: str) -> list:
    for route in app_module.app.routes:
        if getattr(route, "path", None) == path and method in getattr(
            route, "methods", ()
        ):
            return [d.call for d in route.dependant.dependencies]
    raise AssertionError(f"route not found: {method} {path}")


def test_signup_gate_wired_to_register_only():
    register = _route_calls("POST", "/auth/register")
    login = _route_calls("POST", "/auth/jwt/login")
    assert app_module.signup_allowed in register
    assert app_module.signup_allowed not in login
    assert register.index(app_module.rate_limit_register) < register.index(
        app_module.signup_allowed
    )


class StubRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


@pytest.fixture
def stub_redis(monkeypatch):
    r = StubRedis()
    monkeypatch.setattr(app_module, "_get_signup_redis", lambda: r)
    return r


async def test_signup_without_invite_is_403(monkeypatch, stub_redis):
    monkeypatch.setattr(config, "SIGNUP_OPEN", False)
    with pytest.raises(HTTPException) as exc:
        await app_module.signup_allowed(_req({}), None)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Signup is invite-only"


async def test_signup_with_valid_invite_allowed(monkeypatch, stub_redis):
    monkeypatch.setattr(config, "SIGNUP_OPEN", False)
    token = create_invite()
    assert await app_module.signup_allowed(_req({}), token) is True
    with pytest.raises(HTTPException) as exc:
        await app_module.signup_allowed(_req({}), token)
    assert exc.value.status_code == 403


async def test_signup_open_breakglass(monkeypatch, stub_redis):
    monkeypatch.setattr(config, "SIGNUP_OPEN", True)
    assert await app_module.signup_allowed(_req({}), None) is True


async def test_signup_redis_down_is_503(monkeypatch):
    import redis

    class Down:
        async def set(self, *a, **k):
            raise redis.exceptions.ConnectionError("down")

    monkeypatch.setattr(config, "SIGNUP_OPEN", False)
    monkeypatch.setattr(app_module, "_get_signup_redis", lambda: Down())
    with pytest.raises(HTTPException) as exc:
        await app_module.signup_allowed(_req({}), create_invite())
    assert exc.value.status_code == 503


async def test_signup_redis_timeout_is_503(monkeypatch):
    class Slow:
        async def set(self, *a, **k):
            raise TimeoutError("slow")

    monkeypatch.setattr(config, "SIGNUP_OPEN", False)
    monkeypatch.setattr(app_module, "_get_signup_redis", lambda: Slow())
    with pytest.raises(HTTPException) as exc:
        await app_module.signup_allowed(_req({}), create_invite())
    assert exc.value.status_code == 503


async def test_signup_redis_runtime_error_is_503(monkeypatch):
    class Closed:
        async def set(self, *a, **k):
            raise RuntimeError("Event loop is closed")

    monkeypatch.setattr(config, "SIGNUP_OPEN", False)
    monkeypatch.setattr(app_module, "_get_signup_redis", lambda: Closed())
    with pytest.raises(HTTPException) as exc:
        await app_module.signup_allowed(_req({}), create_invite())
    assert exc.value.status_code == 503
