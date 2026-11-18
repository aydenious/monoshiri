import logging

import litellm
from langchain_litellm import ChatLiteLLM
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.config import config
from app.db import Model, Workspace
from app.services.auto_model_pin_service import (
    auto_model_candidates,
    choose_auto_model_candidate,
)
from app.services.llm_router_service import (
    AUTO_MODE_ID,
    ChatLiteLLMRouter,
    is_auto_mode,
)
from app.services.model_capabilities import has_capability
from app.services.model_resolver import to_litellm
from app.services.token_tracking_service import token_tracker

# Configure litellm to automatically drop unsupported parameters
litellm.drop_params = True

# Memory controls: prevent unbounded internal accumulation
litellm.telemetry = False
litellm.cache = None
litellm.failure_callback = []
litellm.input_callback = []

litellm.callbacks = [token_tracker]

logger = logging.getLogger(__name__)


class LLMRole:
    AGENT = "agent"  # For agent/chat operations


def get_global_model(model_id: int) -> dict | None:
    return next((m for m in config.GLOBAL_MODELS if m.get("id") == model_id), None)


def get_global_connection(connection_id: int) -> dict | None:
    return next(
        (c for c in config.GLOBAL_CONNECTIONS if c.get("id") == connection_id),
        None,
    )


def _has_capability(model: dict | Model, capability: str) -> bool:
    return has_capability(model, capability)


def _chat_litellm_from_resolved(
    *,
    conn: dict | object,
    model_id: str,
    disable_streaming: bool = False,
) -> tuple[str, dict]:
    model_string, resolved_kwargs = to_litellm(conn, model_id)
    litellm_kwargs = {"model": model_string, **resolved_kwargs}
    if disable_streaming:
        litellm_kwargs["disable_streaming"] = True
    return model_string, litellm_kwargs


async def _get_db_model(
    session: AsyncSession,
    model_id: int,
    workspace: Workspace,
) -> Model | None:
    result = await session.execute(
        select(Model)
        .options(selectinload(Model.connection))
        .where(Model.id == model_id, Model.enabled.is_(True))
    )
    model = result.scalars().first()
    if not model or not model.connection or not model.connection.enabled:
        return None
    conn = model.connection
    if conn.workspace_id and conn.workspace_id != workspace.id:
        return None
    if conn.user_id and conn.user_id != workspace.user_id:
        return None
    return model


async def get_workspace_llm_instance(
    session: AsyncSession,
    workspace_id: int,
    role: str,
    disable_streaming: bool = False,
) -> ChatLiteLLM | ChatLiteLLMRouter | None:
    """
    Get a ChatLiteLLM instance for a specific workspace and role.

    LLM preferences are stored at the workspace level and shared by all members.

    If Auto mode (ID 0) is configured, returns a ChatLiteLLMRouter that uses
    LiteLLM Router for automatic load balancing across available providers.

    Args:
        session: Database session
        workspace_id: Workspace ID
        role: LLM role ('agent')

    Returns:
        ChatLiteLLM or ChatLiteLLMRouter instance, or None if not found
    """
    try:
        # Get the workspace with its LLM preferences
        result = await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalars().first()

        if not workspace:
            logger.error(f"Workspace {workspace_id} not found")
            return None

        # Get the appropriate model binding ID based on role
        if role == LLMRole.AGENT:
            llm_config_id = workspace.chat_model_id
        else:
            logger.error(f"Invalid LLM role: {role}")
            return None

        if llm_config_id is None:
            logger.error(f"No {role} LLM configured for workspace {workspace_id}")
            return None

        # Auto mode resolves to one concrete global or BYOK model from the
        # unified model-connections catalog.
        if is_auto_mode(llm_config_id):
            candidates = await auto_model_candidates(
                session,
                workspace_id=workspace_id,
                user_id=workspace.user_id,
                capability="chat",
            )
            if not candidates:
                logger.error("No chat-capable models available for Auto mode")
                return None
            llm_config_id = int(
                choose_auto_model_candidate(candidates, workspace_id)["id"]
            )

        # Check if this is a global virtual model (negative ID)
        if llm_config_id < 0:
            global_model = get_global_model(llm_config_id)
            if not global_model or not _has_capability(global_model, "chat"):
                logger.error(f"Global chat model {llm_config_id} not found")
                return None
            global_connection = get_global_connection(global_model["connection_id"])
            if not global_connection:
                logger.error(
                    "Global connection %s not found for model %s",
                    global_model["connection_id"],
                    llm_config_id,
                )
                return None

            _, litellm_kwargs = _chat_litellm_from_resolved(
                conn=global_connection,
                model_id=global_model["model_id"],
                disable_streaming=disable_streaming,
            )

            from app.agents.chat.runtime.llm_config import (
                SanitizedChatLiteLLM,
            )

            return SanitizedChatLiteLLM(**litellm_kwargs)

        model = await _get_db_model(session, llm_config_id, workspace)
        if not model or not _has_capability(model, "chat"):
            logger.error(
                f"Chat model {llm_config_id} not found in workspace {workspace_id}"
            )
            return None

        _, litellm_kwargs = _chat_litellm_from_resolved(
            conn=model.connection,
            model_id=model.model_id,
            disable_streaming=disable_streaming,
        )

        from app.agents.chat.runtime.llm_config import (
            SanitizedChatLiteLLM,
        )

        return SanitizedChatLiteLLM(**litellm_kwargs)

    except Exception:
        logger.exception(
            "Error getting LLM instance for workspace %s, role %s", workspace_id, role
        )
        return None


async def get_agent_llm(
    session: AsyncSession, workspace_id: int, disable_streaming: bool = False
) -> ChatLiteLLM | ChatLiteLLMRouter | None:
    """Get the workspace's chat model instance."""
    return await get_workspace_llm_instance(
        session,
        workspace_id,
        LLMRole.AGENT,
        disable_streaming=disable_streaming,
    )


async def get_vision_llm(
    session: AsyncSession,
    workspace_id: int,
    *,
    usage_type: str = "vision_extraction",
) -> ChatLiteLLM | ChatLiteLLMRouter | None:
    """Get the workspace's vision LLM instance for screenshot analysis.

    Resolves from the new connection/model role bindings:
    - Auto mode (ID 0): unified global/BYOK model candidate selection
    - Global (negative ID): virtual GLOBAL models from YAML
    - DB (positive ID): Model + Connection tables

    Premium global configs are wrapped in :class:`RecordedVisionLLM` so each
    ``ainvoke`` writes one ``TokenUsage`` row against the workspace owner, as
    before billing was removed. User-owned BYOK configs and free global
    configs are returned unwrapped, so inside a chat turn their tokens stay in
    the turn's own accumulator.
    """
    from app.services.recorded_vision_llm import RecordedVisionLLM

    try:
        result = await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalars().first()
        if not workspace:
            logger.error(f"Workspace {workspace_id} not found")
            return None

        owner_user_id = workspace.user_id

        # Prefer the selected chat model when it is vision-capable.
        chat_model_id = workspace.chat_model_id
        if chat_model_id and chat_model_id != AUTO_MODE_ID:
            if chat_model_id < 0:
                chat_model = get_global_model(chat_model_id)
                if chat_model and _has_capability(chat_model, "vision"):
                    global_connection = get_global_connection(
                        chat_model["connection_id"]
                    )
                    if global_connection:
                        model_string, litellm_kwargs = _chat_litellm_from_resolved(
                            conn=global_connection,
                            model_id=chat_model["model_id"],
                        )
                        from app.agents.chat.runtime.llm_config import (
                            SanitizedChatLiteLLM,
                        )

                        return SanitizedChatLiteLLM(**litellm_kwargs)
            else:
                chat_model = await _get_db_model(session, chat_model_id, workspace)
                if chat_model and _has_capability(chat_model, "vision"):
                    _, litellm_kwargs = _chat_litellm_from_resolved(
                        conn=chat_model.connection,
                        model_id=chat_model.model_id,
                    )
                    from app.agents.chat.runtime.llm_config import (
                        SanitizedChatLiteLLM,
                    )

                    return SanitizedChatLiteLLM(**litellm_kwargs)

        config_id = workspace.vision_model_id
        if config_id is None:
            logger.error(f"No vision LLM configured for workspace {workspace_id}")
            return None

        if config_id == AUTO_MODE_ID:
            candidates = await auto_model_candidates(
                session,
                workspace_id=workspace_id,
                user_id=owner_user_id,
                capability="vision",
            )
            if not candidates:
                logger.error("No vision-capable models available for Auto mode")
                return None
            config_id = int(choose_auto_model_candidate(candidates, workspace_id)["id"])

        if config_id < 0:
            global_model = get_global_model(config_id)
            if not global_model or not _has_capability(global_model, "vision"):
                logger.error(f"Global vision model {config_id} not found")
                return None

            global_connection = get_global_connection(global_model["connection_id"])
            if not global_connection:
                logger.error(
                    "Global connection %s not found for model %s",
                    global_model["connection_id"],
                    config_id,
                )
                return None

            model_string, litellm_kwargs = _chat_litellm_from_resolved(
                conn=global_connection,
                model_id=global_model["model_id"],
            )

            from app.agents.chat.runtime.llm_config import (
                SanitizedChatLiteLLM,
            )

            inner_llm = SanitizedChatLiteLLM(**litellm_kwargs)
            billing_tier = str(global_model.get("billing_tier", "free")).lower()
            if billing_tier == "premium":
                return RecordedVisionLLM(
                    inner_llm,
                    user_id=owner_user_id,
                    workspace_id=workspace_id,
                    base_model=model_string,
                    usage_type=usage_type,
                )
            return inner_llm

        model = await _get_db_model(session, config_id, workspace)
        if not model or not _has_capability(model, "vision"):
            logger.error(
                f"Vision model {config_id} not found in workspace {workspace_id}"
            )
            return None

        _, litellm_kwargs = _chat_litellm_from_resolved(
            conn=model.connection,
            model_id=model.model_id,
        )

        from app.agents.chat.runtime.llm_config import (
            SanitizedChatLiteLLM,
        )

        return SanitizedChatLiteLLM(**litellm_kwargs)

    except Exception:
        logger.exception("Error getting vision LLM for workspace %s", workspace_id)
        return None
