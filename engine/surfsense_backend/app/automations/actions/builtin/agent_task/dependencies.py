"""Build the per-invocation dependencies the multi_agent_chat factory needs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Workspace
from app.tasks.chat.streaming.flows.shared.llm_bundle import load_llm_bundle
from app.tasks.chat.streaming.flows.shared.pre_stream_setup import (
    setup_connector_service,
)


class DependencyError(Exception):
    """An external dependency (LLM config, connector service, ...) refused to load."""


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    """Everything ``create_multi_agent_chat_deep_agent`` needs from the environment."""

    llm: Any
    agent_config: Any
    connector_service: Any
    checkpointer: Any


async def build_dependencies(
    *,
    session: AsyncSession,
    workspace_id: int,
    chat_model_id: int | None = None,
) -> AgentDependencies:
    """Load the LLM bundle, connector service, and a per-invoke in-memory checkpointer.

    Resolves the chat model from the automation's *captured* model snapshot
    (``chat_model_id``) so runs are insulated from later chat/workspace model
    changes. A captured model this install can no longer resolve fails the run
    clearly through ``load_llm_bundle``.

    When ``chat_model_id`` is ``None`` (no captured snapshot — defensive fallback),
    fall back to the live workspace's ``chat_model_id``.
    """
    if chat_model_id is not None:
        resolved_chat_model_id = chat_model_id or 0
    else:
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise DependencyError(f"workspace {workspace_id} not found")
        resolved_chat_model_id = workspace.chat_model_id or 0

    llm, agent_config, err = await load_llm_bundle(
        session,
        config_id=resolved_chat_model_id,
        workspace_id=workspace_id,
    )
    if err is not None or llm is None:
        raise DependencyError(err or "failed to load chat model config")

    connector_service = await setup_connector_service(
        session, workspace_id=workspace_id
    )
    # One-shot runs on a fresh thread don't outlive a single execution, so an
    # in-memory checkpointer suffices. Ongoing chat-thread turns that need
    # durable memory use the shared Postgres checkpointer via stream_new_chat.
    checkpointer = InMemorySaver()
    return AgentDependencies(
        llm=llm,
        agent_config=agent_config,
        connector_service=connector_service,
        checkpointer=checkpointer,
    )
