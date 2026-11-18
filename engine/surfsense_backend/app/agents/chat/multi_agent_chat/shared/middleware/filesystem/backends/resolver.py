"""Filesystem backend resolver for the cloud filesystem."""

from __future__ import annotations

from collections.abc import Callable

from deepagents.backends.protocol import BackendProtocol
from deepagents.backends.state import StateBackend
from langgraph.prebuilt.tool_node import ToolRuntime

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.git_tree import (
    GitTreeBackend,
)
from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.backends.kb_postgres import (
    KBPostgresBackend,
)


def build_backend_resolver(
    *,
    workspace_id: int | None = None,
    knowledge_store_enabled: bool = False,
) -> Callable[[ToolRuntime], BackendProtocol]:
    """Create the deepagents backend resolver for the cloud filesystem.

    The resolver returns a fresh :class:`KBPostgresBackend`
    bound to the current ``runtime`` so the backend can read staging state
    (``staged_dirs``, ``pending_moves``, ``files`` cache, ``kb_anon_doc``)
    for each tool call — or, when ``knowledge_store_enabled``, a
    :class:`GitTreeBackend` serving the turn's private working copy. The
    caller resolves that flag once per turn (global switch AND workspace
    flip), so a turn keeps one backend for its whole lifetime.
    When no ``workspace_id`` is provided, the resolver falls back to
    :class:`StateBackend` (used by sub-agents and tests that don't need
    DB-backed reads).
    """

    if workspace_id is not None:
        if knowledge_store_enabled:

            def _resolve_git_tree(runtime: ToolRuntime) -> BackendProtocol:
                return GitTreeBackend(workspace_id, runtime)

            return _resolve_git_tree

        def _resolve_kb(runtime: ToolRuntime) -> BackendProtocol:
            return KBPostgresBackend(workspace_id, runtime)

        return _resolve_kb

    def _resolve_state(runtime: ToolRuntime) -> StateBackend:
        return StateBackend(runtime)

    return _resolve_state
