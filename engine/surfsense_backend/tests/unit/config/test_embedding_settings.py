import pytest

from app.config.embedding_settings import (
    build_embedding_kwargs,
    check_embedding_dimension_config,
    resolve_embedding_base_url,
    resolve_embedding_dimension,
)

pytestmark = pytest.mark.unit

KNOWN_DIMENSIONS = {"text-embedding-3-small": 1536}


def test_resolve_embedding_base_url_prefers_generic_value() -> None:
    environ = {
        "EMBEDDING_BASE_URL": " http://embed-host:11434 ",
        "OLLAMA_EMBEDDING_BASE_URL": "http://ollama-embed:11434",
    }

    assert resolve_embedding_base_url(environ) == "http://embed-host:11434"


def test_resolve_embedding_base_url_falls_back_to_ollama_specific_value() -> None:
    environ = {
        "EMBEDDING_BASE_URL": " ",
        "OLLAMA_EMBEDDING_BASE_URL": "http://ollama-embed:11434",
    }

    assert resolve_embedding_base_url(environ) == "http://ollama-embed:11434"


def test_build_embedding_kwargs_maps_base_url_to_litellm_api_base() -> None:
    kwargs = build_embedding_kwargs(
        {"EMBEDDING_BASE_URL": "http://host.docker.internal:11435"},
        embedding_model="litellm://ollama/nomic-embed-text",
    )

    assert kwargs == {"api_base": "http://host.docker.internal:11435"}


def test_build_embedding_kwargs_does_not_leak_api_base_to_other_providers() -> None:
    kwargs = build_embedding_kwargs(
        {"EMBEDDING_BASE_URL": "http://host.docker.internal:11435"},
        embedding_model="cohere://embed-english-light-v3.0",
    )

    assert kwargs == {}


def test_build_embedding_kwargs_preserves_azure_settings() -> None:
    kwargs = build_embedding_kwargs(
        {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "test-key",
        },
        embedding_model="azure_openai://text-embedding-3-small",
    )

    assert kwargs == {
        "azure_endpoint": "https://example.openai.azure.com",
        "azure_api_key": "test-key",
    }


def test_build_embedding_kwargs_combines_litellm_and_azure_env_when_set() -> None:
    kwargs = build_embedding_kwargs(
        {
            "EMBEDDING_BASE_URL": "http://host.docker.internal:4000/v1",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
            "AZURE_OPENAI_API_KEY": "test-key",
        },
        embedding_model="litellm://openai/text-embedding-3-small",
    )

    assert kwargs == {
        "api_base": "http://host.docker.internal:4000/v1",
        "azure_endpoint": "https://example.openai.azure.com",
        "azure_api_key": "test-key",
    }


def test_resolve_embedding_dimension_unset_is_none() -> None:
    assert resolve_embedding_dimension({}) is None


def test_resolve_embedding_dimension_blank_is_none() -> None:
    assert resolve_embedding_dimension({"EMBEDDING_DIMENSION": "   "}) is None


def test_resolve_embedding_dimension_trims_whitespace() -> None:
    assert resolve_embedding_dimension({"EMBEDDING_DIMENSION": " 2000 "}) == 2000


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "2000.5"])
def test_resolve_embedding_dimension_rejects_invalid_value(bad: str) -> None:
    with pytest.raises(ValueError, match="EMBEDDING_DIMENSION") as excinfo:
        resolve_embedding_dimension({"EMBEDDING_DIMENSION": bad})

    assert bad in str(excinfo.value)


def test_build_embedding_kwargs_pins_dimension_for_litellm() -> None:
    kwargs = build_embedding_kwargs(
        {
            "EMBEDDING_BASE_URL": "http://embed-proxy:11440",
            "EMBEDDING_DIMENSION": "2000",
        },
        embedding_model="litellm://openai/qwen3-embedding-8b",
    )

    assert kwargs == {"api_base": "http://embed-proxy:11440", "dimension": 2000}


def test_build_embedding_kwargs_pins_dimension_without_base_url() -> None:
    kwargs = build_embedding_kwargs(
        {"EMBEDDING_DIMENSION": "2000"},
        embedding_model="litellm://openai/qwen3-embedding-8b",
    )

    assert kwargs == {"dimension": 2000}


def test_build_embedding_kwargs_does_not_leak_dimension_to_other_providers() -> None:
    kwargs = build_embedding_kwargs(
        {"EMBEDDING_DIMENSION": "2000"},
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    )

    assert "dimension" not in kwargs


def test_check_embedding_dimension_config_rejects_unknown_litellm_model() -> None:
    with pytest.raises(RuntimeError, match="EMBEDDING_DIMENSION"):
        check_embedding_dimension_config(
            "litellm://openai/qwen3-embedding-8b", None, KNOWN_DIMENSIONS
        )


def test_check_embedding_dimension_config_allows_known_litellm_model() -> None:
    check_embedding_dimension_config(
        "litellm://text-embedding-3-small", None, KNOWN_DIMENSIONS
    )


def test_check_embedding_dimension_config_allows_configured_litellm_model() -> None:
    check_embedding_dimension_config(
        "litellm://openai/qwen3-embedding-8b", 2000, KNOWN_DIMENSIONS
    )


def test_check_embedding_dimension_config_allows_local_model_without_dimension() -> None:
    check_embedding_dimension_config(
        "sentence-transformers/all-MiniLM-L6-v2", None, KNOWN_DIMENSIONS
    )


def test_check_embedding_dimension_config_rejects_instance_mismatch() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        check_embedding_dimension_config(
            "litellm://openai/qwen3-embedding-8b",
            2000,
            KNOWN_DIMENSIONS,
            instance_dimension=1536,
        )

    message = str(excinfo.value)
    assert "2000" in message
    assert "1536" in message


def test_litellm_embeddings_with_dimension_makes_no_network_call(monkeypatch) -> None:
    import litellm
    from chonkie import LiteLLMEmbeddings

    def _fail_if_called(*args, **kwargs):
        pytest.fail("litellm.embedding must not be called when dimension is given")

    monkeypatch.setattr(litellm, "embedding", _fail_if_called)
    # Tokenizer setup is unrelated to dimension detection; keep the test offline.
    monkeypatch.setattr(LiteLLMEmbeddings, "_initialize_tokenizer", lambda self: None)

    embeddings = LiteLLMEmbeddings(model="text-embedding-3-small", dimension=2000)

    assert embeddings.dimension == 2000
