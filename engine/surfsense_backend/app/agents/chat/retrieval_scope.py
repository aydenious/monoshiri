"""Per-turn retrieval policy shared by chat routes and agent construction."""

from __future__ import annotations

from enum import StrEnum


class RetrievalScope(StrEnum):
    """Sources the agent may read while answering one user turn."""

    DOCUMENTS = "documents"
    WEB = "web"
    ALL = "all"

    @property
    def allows_documents(self) -> bool:
        return self in {RetrievalScope.DOCUMENTS, RetrievalScope.ALL}

    @property
    def allows_web(self) -> bool:
        return self in {RetrievalScope.WEB, RetrievalScope.ALL}


# These specialists exist to retrieve public web/platform data.
WEB_RETRIEVAL_SUBAGENTS = frozenset(
    {
        "google_search",
        "web_crawler",
        "youtube",
    }
)

_KB_RETRIEVAL_TOOLS = frozenset(
    {
        "execute_code",
        "list_tree",
        "ls",
        "read_file",
        "search_knowledge_base",
    }
)
def excluded_retrieval_subagents(scope: RetrievalScope) -> set[str]:
    """Whole specialists to omit because every operation retrieves web data."""

    return set() if scope.allows_web else set(WEB_RETRIEVAL_SUBAGENTS)


def denied_retrieval_tools(
    scope: RetrievalScope,
    *,
    subagent_name: str,
) -> set[str]:
    """Return tool names that a mixed-capability agent may not read with."""

    denied: set[str] = set()
    if not scope.allows_documents and subagent_name.startswith("knowledge_base"):
        denied.update(_KB_RETRIEVAL_TOOLS)
    return denied


def retrieval_scope_prompt(scope: RetrievalScope) -> str:
    """Model-facing explanation; runtime deny rules remain authoritative."""

    if scope is RetrievalScope.DOCUMENTS:
        return (
            "\n<retrieval_scope>Use only the workspace knowledge base for "
            "retrieval. Do not retrieve public web data. Knowledge-base write "
            "actions remain available when explicitly requested."
            "</retrieval_scope>\n"
        )
    if scope is RetrievalScope.WEB:
        return (
            "\n<retrieval_scope>Use public web for retrieval. Do not search or "
            "read the workspace knowledge base. Knowledge-base write actions "
            "remain available when explicitly requested.</retrieval_scope>\n"
        )
    return (
        "\n<retrieval_scope>Workspace documents and the public web are all "
        "available for retrieval.</retrieval_scope>\n"
    )


__all__ = [
    "RetrievalScope",
    "denied_retrieval_tools",
    "excluded_retrieval_subagents",
    "retrieval_scope_prompt",
]
