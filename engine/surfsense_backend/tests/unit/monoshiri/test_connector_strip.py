"""Task 7: the third-party connector surface is gone; KB search and local-folder indexing stay."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_connector_service_keeps_only_discovery_methods():
    from app.services.connector_service import ConnectorService

    for name in ("get_available_connectors", "get_available_document_types"):
        assert hasattr(ConnectorService, name), name
    for name in (
        "search_files",
        "_combined_rrf_search",
        "search_slack",
        "search_notion",
        "search_github",
    ):
        assert not hasattr(ConnectorService, name), name


def test_local_folder_indexer_keeps_only_upload_path():
    mod = importlib.import_module("app.tasks.local_folder.indexer")
    assert hasattr(mod, "index_uploaded_files")
    for name in ("index_local_folder", "scan_folder"):
        assert not hasattr(mod, name), name
    assert not (APP_ROOT / "tasks" / "connector_indexers").exists()


APP_ROOT = Path(__file__).resolve().parents[3] / "app"


@pytest.mark.parametrize(
    "relpath",
    [
        "connectors",
        "tasks/connector_indexers",
        "tasks/composio_indexer.py",
        "services/composio_service.py",
        "services/mcp_oauth",
        "services/obsidian_plugin_indexer.py",
        "routes/search_source_connectors_routes.py",
        "routes/mcp_oauth_route.py",
        "routes/composio_routes.py",
        "routes/obsidian_plugin_routes.py",
        "routes/circleback_webhook_route.py",
        "agents/chat/multi_agent_chat/subagents/mcp_tools",
        "agents/chat/multi_agent_chat/subagents/builtins/mcp_discovery",
        "agents/chat/multi_agent_chat/shared/tools/mcp",
    ],
)
def test_removed_paths_are_gone(relpath: str):
    # A path check, not an import: a stale editable install can still resolve
    # ``app.connectors`` to an old checkout (this happens in the gate image).
    assert not (APP_ROOT / relpath).exists()


def test_reindex_knowledge_store_task_stays_on_connectors_queue():
    from app.celery_app import CONNECTORS_QUEUE, celery_app
    from app.tasks.celery_tasks.knowledge_store.index_tasks import (
        reindex_knowledge_store,
    )

    assert reindex_knowledge_store.name == "reindex_knowledge_store"
    assert celery_app.conf.task_routes["reindex_knowledge_store"] == {
        "queue": CONNECTORS_QUEUE
    }
