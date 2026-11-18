"""Tripwire: the hosted-service tables stay unmapped and must stay that way.

This install never used the upstream hosted service, so seven of its tables are
dead weight in the ORM: the external-chat gateway (accounts, bindings, inbound
events) and the incentive/purchase bookkeeping (incentive tasks, page purchases,
credit purchases, signup credit claims). The models were deleted from
``app/db.py``; the tables themselves stay in the live DB, because the live
schema came from ``create_all()`` and this fork never runs Alembic.

If one of these comes back, it came back by accident. Restore it only together
with the feature that needs it, and delete this test in the same change.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import configure_mappers

from app import db as db_module

pytestmark = pytest.mark.unit

# Class and enum names removed from ``app/db.py``.
REMOVED_NAMES = (
    "ExternalChatAccount",
    "ExternalChatBinding",
    "ExternalChatInboundEvent",
    "ExternalChatPlatform",
    "ExternalChatAccountMode",
    "ExternalChatHealthStatus",
    "ExternalChatBindingState",
    "ExternalChatPeerKind",
    "ExternalChatEventKind",
    "ExternalChatEventStatus",
    "UserIncentiveTask",
    "IncentiveTaskType",
    "PagePurchase",
    "PagePurchaseStatus",
    "CreditPurchase",
    "CreditPurchaseStatus",
    "SignupCreditClaim",
)

# The seven tables they mapped. They still exist in the live DB.
REMOVED_TABLES = (
    "external_chat_accounts",
    "external_chat_bindings",
    "external_chat_inbound_events",
    "user_incentive_tasks",
    "page_purchases",
    "credit_purchases",
    "signup_credit_claims",
)


@pytest.mark.parametrize("name", REMOVED_NAMES)
def test_removed_class_is_not_an_attribute_of_db(name: str):
    assert not hasattr(db_module, name), f"app.db.{name} is back"


@pytest.mark.parametrize("table", REMOVED_TABLES)
def test_removed_table_is_not_mapped(table: str):
    assert table not in db_module.Base.metadata.tables, f"{table} is mapped again"


def test_thread_binding_column_survives_without_its_foreign_key():
    """Old threads keep listable/openable/chat-able after the gateway is unmapped.

    ``new_chat_threads.external_chat_binding_id`` is a live column with rows on
    it in the live DB. The column stays (nullable, BigInteger); the Python-side
    ForeignKey and relationship go, because an FK naming a table outside the
    metadata raises ``NoReferencedTableError`` at create_all()/flush time. The
    DB keeps its own ``ON DELETE SET NULL`` constraint.
    """

    column = db_module.NewChatThread.__table__.c.external_chat_binding_id
    assert column.nullable
    assert column.foreign_keys == set()


def test_kept_metadata_configures_all_mappers():
    """``cascade="all, delete-orphan"`` and string relationships must all resolve."""

    configure_mappers()
