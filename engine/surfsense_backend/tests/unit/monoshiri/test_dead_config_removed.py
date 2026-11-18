"""Tripwire: the dead config keys, Stripe leftovers and unused rbac helpers stay gone.

Monoshiri is single-user and never used Stripe: nothing reads the SMTP,
Stripe, TikTok-retry or Keygen-era settings, ``stripe`` is not a dependency
and account erasure no longer calls it. The three rbac helpers that no route
called were deleted with them. If any of these comes back, it came back by
accident — restore it together with the caller that needs it and delete this
test in the same change. ``db.py`` is exempt from the Stripe scan: it keeps the
``stripe_customer_id`` column so the live schema and old rows keep working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.utils.rbac as rbac
from app.config import config

pytestmark = pytest.mark.unit

APP_ROOT = Path(__file__).resolve().parents[3] / "app"

REMOVED_CONFIG_ATTRIBUTES = (
    "STRIPE_SECRET_KEY",
    "SMTP_ENABLED",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_SECURITY",
    "SMTP_TIMEOUT_SECONDS",
    "SMTP_FROM",
    "TIKTOK_LISTING_MAX_ATTEMPTS",
    "UNSTRUCTURED_API_KEY",
)

REMOVED_RBAC_HELPERS = (
    "is_workspace_owner",
    "get_workspace_with_access_check",
    "get_owner_role",
)


@pytest.mark.parametrize("name", REMOVED_CONFIG_ATTRIBUTES)
def test_dead_config_attribute_is_gone(name: str) -> None:
    assert not hasattr(config, name), f"config.{name} is back"


@pytest.mark.parametrize("name", REMOVED_RBAC_HELPERS)
def test_unused_rbac_helper_is_gone(name: str) -> None:
    assert not hasattr(rbac, name), f"app.utils.rbac.{name} is back"


def test_no_app_module_imports_stripe() -> None:
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name == "db.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "import stripe" in text or "from stripe" in text:
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == []
