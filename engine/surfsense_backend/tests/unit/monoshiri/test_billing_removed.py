"""Tripwire: the credit/wallet and premium-quota billing is gone and must stay gone.

This install is single-user and self-hosted: nothing is charged, metered
against a balance, blocked for lack of credits or reserved against a prepaid
allowance. If one of these modules or identifiers comes back, it came back by
accident — restore it together with the gate that justifies it, and delete this
test in the same change. ``db.py`` is exempt: the columns and models stay so the
live schema and old rows keep working, and ``billing_tier`` itself is not
forbidden because it is still a config value and an API field.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP_ROOT = Path(__file__).resolve().parents[3] / "app"

# Module path -> source file, relative to ``app/``.
DELETED_MODULES = {
    "app.services.etl_credit_service": "services/etl_credit_service.py",
    "app.services.web_crawl_credit_service": "services/web_crawl_credit_service.py",
    "app.services.platform_scrape_credit_service": (
        "services/platform_scrape_credit_service.py"
    ),
    "app.services.wallet_credit": "services/wallet_credit.py",
    "app.services.image_gen_billing": "services/image_gen_billing.py",
    "app.capabilities.core.billing": "capabilities/core/billing.py",
    "app.notifications.service.handlers.insufficient_credits": (
        "notifications/service/handlers/insufficient_credits.py"
    ),
    "app.notifications.service.messages.insufficient_credits": (
        "notifications/service/messages/insufficient_credits.py"
    ),
    "app.notifications.service.handlers.auto_reload_failed": (
        "notifications/service/handlers/auto_reload_failed.py"
    ),
    "app.notifications.service.messages.auto_reload_failed": (
        "notifications/service/messages/auto_reload_failed.py"
    ),
    "app.services.billable_calls": "services/billable_calls.py",
    "app.services.token_quota_service": "services/token_quota_service.py",
    "app.services.quota_checked_vision_llm": "services/quota_checked_vision_llm.py",
    "app.tasks.chat.streaming.flows.shared.premium_quota": (
        "tasks/chat/streaming/flows/shared/premium_quota.py"
    ),
    "app.automations.services.model_policy": "automations/services/model_policy.py",
}

DELETED_IDENTIFIERS = (
    "etl_credit",
    "wallet_credit",
    "web_crawl_credit",
    "platform_scrape_credit",
    "InsufficientCreditsError",
    "charge_capability",
    "gate_capability",
    "premium_quota",
    "token_quota_service",
    "quota_checked_vision",
    "quota_reserve_micros",
    "reserve_micros",
    "QuotaInsufficientError",
    "BillingSettlementError",
    "TokenQuotaService",
    "credit_get_usage",
    "credit_reserve",
    "estimate_call_reserve_micros",
    "services.billable_calls",
    "services import billable_calls",
    "billable_call(",
    "needs_credit_quota",
    "force_repin_free",
    "_resolve_agent_billing_for_workspace",
    "model_policy",
    "AutomationModelPolicyError",
    "AutoReloadFailedNotificationHandler",
    "notify_auto_reload_failed",
)


@pytest.mark.parametrize("module,relpath", sorted(DELETED_MODULES.items()))
def test_deleted_billing_module_is_gone(module: str, relpath: str):
    # A path check first: a stale editable install can still resolve a removed
    # module name to an old checkout (this happens in the gate image).
    assert not (APP_ROOT / relpath).exists(), f"{relpath} is back"
    assert importlib.util.find_spec(module) is None, f"{module} is importable again"


def test_no_credit_billing_identifier_survives_in_app():
    """No app module (``db.py`` excepted: the wallet columns stay) names them."""
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.name == "db.py":
            continue
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(APP_ROOT)}: {identifier}"
            for identifier in DELETED_IDENTIFIERS
            if identifier in text
        )
    assert offenders == []
