import pytest

from app.agents.chat.retrieval_scope import (
    RetrievalScope,
    denied_retrieval_tools,
    excluded_retrieval_subagents,
)

pytestmark = pytest.mark.unit


def test_documents_scope_removes_public_web_specialists() -> None:
    excluded = excluded_retrieval_subagents(RetrievalScope.DOCUMENTS)
    assert {"google_search", "web_crawler", "youtube"} <= excluded
    assert excluded_retrieval_subagents(RetrievalScope.WEB) == set()
    assert excluded_retrieval_subagents(RetrievalScope.ALL) == set()


def test_web_scope_denies_knowledge_reads_but_not_writes() -> None:
    denied = denied_retrieval_tools(
        RetrievalScope.WEB,
        subagent_name="knowledge_base",
    )

    assert {"search_knowledge_base", "read_file", "execute_code"} <= denied
    assert "write_file" not in denied
