"""Vision LLM proxy that records the token usage of every ``ainvoke``.

Used by :func:`app.services.llm_service.get_vision_llm` so callers in the
indexing pipeline (file processors, connector indexers, etl pipeline) can
keep invoking the LLM exactly the way they do today — ``await llm.ainvoke(...)``
— without threading ``user_id`` through every parser. The wrapper looks like
a chat model from the outside; on the inside it routes each call through
``recorded_llm_call`` so one ``TokenUsage`` audit row is written per call.

Why a wrapper instead of plumbing ``user_id`` through every caller:

* The indexer ecosystem has 8+ entry points (Google Drive, OneDrive,
  Dropbox, local-folder, file-processor, ETL pipeline) each calling
  ``parse_with_vision_llm(...)``. Adding a ``user_id`` argument to each is
  invasive, error-prone, and easy for a future indexer to forget.
* The row belongs to the *workspace owner*, not the triggering user, so
  ``user_id`` is fully derivable from the search space the caller is already
  operating on. The wrapper captures it once at construction time.
* ``langchain_litellm.ChatLiteLLM`` has no public hook for "before each
  call run this coroutine"; subclassing isn't safe across versions because
  it derives from ``BaseChatModel`` which expects specific Pydantic shapes.
  Composition via attribute proxying (``__getattr__``) is robust to
  upstream changes — every method other than ``ainvoke`` falls through to
  the inner LLM unchanged.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.llm_usage import recorded_llm_call


class RecordedVisionLLM:
    """Composition wrapper that records token usage on every ``ainvoke``.

    Anything other than ``ainvoke`` is forwarded to the inner model so
    ``invoke`` (sync), ``astream``, ``with_structured_output``, etc. all
    still work — they simply run unrecorded, which is fine because the
    indexing pipeline only ever calls ``ainvoke`` today.
    """

    def __init__(
        self,
        inner_llm: Any,
        *,
        user_id: UUID,
        workspace_id: int,
        base_model: str,
        usage_type: str = "vision_extraction",
    ) -> None:
        self._inner = inner_llm
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._base_model = base_model
        self._usage_type = usage_type

    async def ainvoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        """Proxied async invoke that runs the underlying call inside
        ``recorded_llm_call``."""
        async with recorded_llm_call(
            user_id=self._user_id,
            workspace_id=self._workspace_id,
            usage_type=self._usage_type,
            call_details={"model": self._base_model},
        ):
            return await self._inner.ainvoke(input, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Forward everything else (``invoke``, ``astream``, ``bind``,
        ``with_structured_output``, …) to the inner model.

        ``__getattr__`` is only consulted when the attribute is *not*
        already found on the proxy, which is exactly the contract we
        want — methods we override stay on the proxy, the rest fall
        through.
        """
        return getattr(self._inner, name)


__all__ = ["RecordedVisionLLM"]
