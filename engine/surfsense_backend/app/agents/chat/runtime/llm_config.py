"""
LLM configuration utilities for SurfSense agents.

This module provides functions for loading LLM configurations from:
1. Auto mode (ID 0) - Resolved by callers to a concrete model-connection model
2. YAML files (global configs with negative IDs)
3. Database model-connections table (user-created configs with positive IDs)

It also provides utilities for creating ChatLiteLLM instances and
managing prompt configurations.
"""

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_litellm import ChatLiteLLM
from litellm import get_model_info

from app.services.context_admission import (
    SURFSENSE_UNKNOWN_MODEL_MAX_INPUT_TOKENS,
    admit_langchain_messages,
    compute_tool_tokens,
)
from app.services.llm_router_service import (
    AUTO_MODE_ID,
    _sanitize_content,
)

logger = logging.getLogger(__name__)


def _sanitize_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Sanitize content on every message so it is safe for any provider.

    Handles three cross-provider incompatibilities:
    - List content with provider-specific blocks (e.g. ``thinking``)
    - List content with bare strings or empty text blocks
    - AI messages with empty content + tool calls: some providers (Bedrock)
      convert ``""`` to ``[{"type":"text","text":""}]`` server-side then
      reject the blank text.  The OpenAI spec says ``content`` should be
      ``null`` when an assistant message only carries tool calls.
    """
    sanitized: list[BaseMessage] = []
    for msg in messages:
        next_msg = msg.model_copy(deep=True)
        if isinstance(next_msg.content, list):
            next_msg.content = _sanitize_content(next_msg.content)
        if (
            isinstance(next_msg, AIMessage)
            and (not next_msg.content or next_msg.content == "")
            and getattr(next_msg, "tool_calls", None)
        ):
            next_msg.content = None  # type: ignore[assignment]
        sanitized.append(next_msg)
    return sanitized


class SanitizedChatLiteLLM(ChatLiteLLM):
    """ChatLiteLLM subclass that strips provider-specific content blocks
    (e.g. ``thinking`` from reasoning models) and normalises bare strings
    in content arrays before forwarding to the underlying provider."""

    def _count_context_tokens(self, messages: list[dict[str, Any]]) -> int | None:
        from litellm import token_counter

        profile = getattr(self, "profile", None)
        if not isinstance(profile, dict):
            return None
        model_names = profile.get("token_count_models")
        if not isinstance(model_names, list):
            model_names = [profile.get("token_count_model")]
        counts: list[int] = []
        for model_name in model_names:
            if not isinstance(model_name, str) or not model_name:
                continue
            try:
                counts.append(token_counter(messages=messages, model=model_name))
            except Exception:
                logger.debug(
                    "llm_config: token count failed for one model name, trying the next",
                    exc_info=True,
                )
                continue
        return max(counts) if counts else None

    def _admit_messages(
        self, messages: list[BaseMessage], tools: Any = None
    ) -> list[BaseMessage]:
        sanitized = _sanitize_messages(messages)
        profile = getattr(self, "profile", None)
        max_input_tokens = (
            profile.get("max_input_tokens") if isinstance(profile, dict) else None
        )
        if (
            not isinstance(max_input_tokens, int)
            or isinstance(max_input_tokens, bool)
            or max_input_tokens <= 0
        ):
            return sanitized
        return admit_langchain_messages(
            sanitized,
            sanitize_content=_sanitize_content,
            count_tokens=self._count_context_tokens,
            max_input_tokens=max_input_tokens,
            reserved_tokens=compute_tool_tokens(tools, self._count_context_tokens),
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return super()._generate(
            self._admit_messages(messages, kwargs.get("tools")),
            stop,
            run_manager,
            **kwargs,
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from super()._stream(
            self._admit_messages(messages, kwargs.get("tools")),
            stop,
            run_manager,
            **kwargs,
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        async for chunk in super()._astream(
            self._admit_messages(messages, kwargs.get("tools")),
            stop,
            run_manager,
            **kwargs,
        ):
            yield chunk

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        stream: bool | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await super()._agenerate(
            self._admit_messages(messages, kwargs.get("tools")),
            stop=stop,
            run_manager=run_manager,
            stream=stream,
            **kwargs,
        )


def resolve_max_input_tokens(
    model_string: str,
    persisted_max_input_tokens: int | None = None,
) -> int:
    """Resolve the model budget: stored limit, then catalog, then fallback.

    Deliberately ignores how much context a local runtime currently has
    loaded: LM Studio and Ollama load, unload, and JIT-reload instances at
    will, so any value we could read here goes stale between requests. A
    runtime whose loaded context is smaller than this budget rejects the
    request itself, and that error is classified as MODEL_CONTEXT_LIMIT.
    """
    if (
        isinstance(persisted_max_input_tokens, int)
        and not isinstance(persisted_max_input_tokens, bool)
        and persisted_max_input_tokens > 0
    ):
        return persisted_max_input_tokens

    try:
        catalog_value = get_model_info(model_string).get("max_input_tokens")
        if (
            isinstance(catalog_value, int)
            and not isinstance(catalog_value, bool)
            and catalog_value > 0
        ):
            return catalog_value
    except Exception:
        logger.debug(
            "llm_config: model catalog has no max input tokens, using the default",
            exc_info=True,
        )
    return SURFSENSE_UNKNOWN_MODEL_MAX_INPUT_TOKENS


def _attach_model_profile(
    llm: ChatLiteLLM,
    model_string: str,
    persisted_max_input_tokens: int | None = None,
) -> int:
    """Attach normalized context metadata and return the effective limit."""
    max_input_tokens = resolve_max_input_tokens(
        model_string,
        persisted_max_input_tokens=persisted_max_input_tokens,
    )
    llm.profile = {
        "max_input_tokens": max_input_tokens,
        "max_input_tokens_upper": max_input_tokens,
        "token_count_model": model_string,
        "token_count_models": [model_string],
    }
    return max_input_tokens


@dataclass
class AgentConfig:
    """
    Complete configuration for the SurfSense agent.

    This combines resolved model settings with prompt configuration.
    Supports Auto mode metadata (ID 0). Runtime callers must resolve Auto to
    a concrete global or BYOK model before constructing ChatLiteLLM.
    """

    # LLM Model Settings
    provider: str
    model_name: str
    api_key: str
    api_base: str | None = None
    custom_provider: str | None = None
    litellm_params: dict | None = None

    # Prompt Configuration
    system_instructions: str | None = None
    use_default_system_instructions: bool = True
    citations_enabled: bool = True

    # Metadata
    config_id: int | None = None
    config_name: str | None = None

    # Auto mode flag
    is_auto_mode: bool = False

    # Policy
    anonymous_enabled: bool = False

    # Default-allow: only the streaming safety net (is_known_text_only_chat_model)
    # actually blocks on False, so defaulting False would silently hide
    # vision-capable models. Resolved via derive_supports_image_input.
    supports_image_input: bool = True

    @classmethod
    def from_auto_mode(cls) -> "AgentConfig":
        """Build an AgentConfig for Auto mode (LiteLLM Router load balancing)."""
        return cls(
            provider="AUTO",
            model_name="auto",
            api_key="",  # Not needed for router
            api_base=None,
            custom_provider=None,
            litellm_params=None,
            system_instructions=None,
            use_default_system_instructions=True,
            citations_enabled=True,
            config_id=AUTO_MODE_ID,
            config_name="Auto",
            is_auto_mode=True,
            anonymous_enabled=False,
            # Auto fails over across the pool, so a non-vision deployment's 404
            # is just an allowed_fails event rather than a hard block.
            supports_image_input=True,
        )
