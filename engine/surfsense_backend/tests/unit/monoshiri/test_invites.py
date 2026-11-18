import time

import jwt
import pytest
from fastapi_users.jwt import generate_jwt

from app.config import config
from app.monoshiri import __main__ as cli
from app.monoshiri.invites import (
    JTI_RE,
    InviteError,
    consume_invite,
    create_invite,
)

pytestmark = pytest.mark.unit


class StubRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ex: dict[str, int | None] = {}

    async def set(self, key, value, nx=False, ex=None):
        self.ex[key] = ex
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


async def test_invite_roundtrip():
    r = StubRedis()
    token = create_invite()
    await consume_invite(token, r)
    assert len(r.data) == 1
    with pytest.raises(InviteError) as exc:
        await consume_invite(token, r)
    assert exc.value.reason == "used"


async def test_invite_expired():
    with pytest.raises(InviteError) as exc:
        await consume_invite(create_invite(ttl_seconds=-1), StubRedis())
    assert exc.value.reason == "expired"


async def test_invite_bad_signature_or_audience():
    r = StubRedis()
    claims = {"aud": "monoshiri:invite", "jti": "a", "exp": int(time.time()) + 60}
    bad_key = jwt.encode(claims, "other-key", algorithm="HS256")
    bad_aud = jwt.encode(
        {**claims, "aud": "other"}, config.SECRET_KEY, algorithm="HS256"
    )
    login = generate_jwt(
        {"sub": "u", "aud": ["fastapi-users:auth"]}, config.SECRET_KEY, 3600
    )
    for tok in (bad_key, bad_aud, login, "garbage"):
        with pytest.raises(InviteError) as exc:
            await consume_invite(tok, r)
        assert exc.value.reason == "invalid"
    assert r.data == {}


async def test_invite_marker_ttl_covers_token_exp():
    r = StubRedis()
    ttl = 300
    token = create_invite(ttl_seconds=ttl)
    exp = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})[
        "exp"
    ]
    now = time.time()
    await consume_invite(token, r)
    (ex,) = r.ex.values()
    assert 1 <= ex <= ttl + 1
    assert ex >= exp - now


def test_cli_url_format(monkeypatch):
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "https://x.example/")
    url = cli.build_invite_url("tok")
    assert url == "https://x.example/register?invite=tok"


def test_cli_requires_frontend_url(monkeypatch, capsys):
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "")
    with pytest.raises(SystemExit) as exc:
        cli.main(["invite"])
    assert exc.value.code not in (0, None)
    captured = capsys.readouterr()
    assert "NEXT_FRONTEND_URL" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("days", ["0", "-1"])
def test_cli_rejects_non_positive_days(monkeypatch, capsys, days):
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "https://x.example")
    with pytest.raises(SystemExit) as exc:
        cli.main(["invite", "--days", days])
    assert exc.value.code not in (0, None)
    captured = capsys.readouterr()
    assert "--days" in captured.err
    assert captured.out == ""


def test_cli_prints_url(monkeypatch, capsys):
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "https://x.example")
    cli.main(["invite", "--days", "1"])
    assert capsys.readouterr().out.startswith("https://x.example/register?invite=")


def test_invite_token_carries_no_note_or_creator():
    claims = jwt.decode(
        create_invite(),
        config.SECRET_KEY,
        algorithms=["HS256"],
        audience="monoshiri:invite",
    )
    assert set(claims) == {"aud", "jti", "iat", "exp"}
    assert JTI_RE.fullmatch(claims["jti"])


def test_cli_note_goes_to_stderr_not_token(monkeypatch, capsys):
    monkeypatch.setattr(config, "NEXT_FRONTEND_URL", "https://kb.example.org")
    cli.main(["invite", "--note", "for X"])
    out = capsys.readouterr()
    assert out.out.startswith("https://kb.example.org/register?invite=")
    assert "for X" not in out.out
    assert "for X" in out.err
