"""One-call ``Command`` + receipt for the deliverables tools (route and operation are fixed)."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langgraph.types import Command

from app.agents.chat.multi_agent_chat.shared.receipts.command import with_receipt
from app.agents.chat.multi_agent_chat.shared.receipts.receipt import (
    ReceiptStatus,
    make_receipt,
)


def deliverable_command(
    runtime: ToolRuntime,
    payload: dict[str, Any] | str,
    *,
    type: str,
    status: ReceiptStatus,
    **receipt_fields: str | None,
) -> Command:
    """``with_receipt`` for a ``deliverables`` ``generate`` receipt."""
    return with_receipt(
        payload=payload,
        receipt=make_receipt(
            route="deliverables",
            type=type,
            operation="generate",
            status=status,
            **receipt_fields,
        ),
        tool_call_id=runtime.tool_call_id,
    )
