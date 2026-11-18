from unittest.mock import MagicMock

import pytest


@pytest.fixture
def patched_chunker_instance(monkeypatch):
    mock = MagicMock()
    mock.chunk.return_value = [MagicMock(text="prose chunk")]
    monkeypatch.setattr(
        "app.indexing_pipeline.document_chunker.config.chunker_instance", mock
    )
    return mock


@pytest.fixture
def patched_code_chunker_instance(monkeypatch):
    mock = MagicMock()
    mock.chunk.return_value = [MagicMock(text="code chunk")]
    monkeypatch.setattr(
        "app.indexing_pipeline.document_chunker.config.code_chunker_instance", mock
    )
    return mock
