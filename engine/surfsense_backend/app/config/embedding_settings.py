import os
from collections.abc import Mapping

EMBEDDING_BASE_URL_ENV = "EMBEDDING_BASE_URL"
OLLAMA_EMBEDDING_BASE_URL_ENV = "OLLAMA_EMBEDDING_BASE_URL"
EMBEDDING_DIMENSION_ENV = "EMBEDDING_DIMENSION"
LITELLM_EMBEDDING_SCHEME = "litellm://"


def _clean_env_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def resolve_embedding_base_url(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the configured embedding endpoint, if any."""
    environ = os.environ if environ is None else environ
    return _clean_env_value(environ.get(EMBEDDING_BASE_URL_ENV)) or _clean_env_value(
        environ.get(OLLAMA_EMBEDDING_BASE_URL_ENV)
    )


def _supports_embedding_api_base(embedding_model: str | None) -> bool:
    return (embedding_model or "").startswith(LITELLM_EMBEDDING_SCHEME)


def resolve_embedding_dimension(
    environ: Mapping[str, str] | None = None,
) -> int | None:
    """Return EMBEDDING_DIMENSION as a positive integer, or ``None`` if unset.

    A blank value means "not configured". Anything that is not a positive
    integer raises instead of being ignored: a typo must never silently bring
    back chonkie's 1536 fallback.
    """
    environ = os.environ if environ is None else environ
    raw = _clean_env_value(environ.get(EMBEDDING_DIMENSION_ENV))
    if raw is None:
        return None
    try:
        dimension = int(raw)
    except ValueError:
        raise ValueError(
            f"{EMBEDDING_DIMENSION_ENV} must be a positive integer, got {raw!r}"
        ) from None
    if dimension <= 0:
        raise ValueError(
            f"{EMBEDDING_DIMENSION_ENV} must be a positive integer, got {raw!r}"
        )
    return dimension


def check_embedding_dimension_config(
    model: str | None,
    configured: int | None,
    known: Mapping[str, int],
    *,
    instance_dimension: int | None = None,
) -> None:
    """Refuse to start when the embedding dimension would be guessed.

    ``known`` is chonkie's ``LiteLLMEmbeddings.KNOWN_DIMENSIONS``. Called once
    before the embedder is built (so no probe request is made) and once after,
    with ``instance_dimension``, to catch providers that ignore the kwarg.
    """
    if (
        configured is not None
        and instance_dimension is not None
        and instance_dimension != configured
    ):
        raise RuntimeError(
            f"{EMBEDDING_DIMENSION_ENV}={configured} but embedding model "
            f"{model!r} reports dimension {instance_dimension}; refusing to "
            "start because the database columns and queries would disagree."
        )

    if configured is not None:
        return
    if not (model or "").startswith(LITELLM_EMBEDDING_SCHEME):
        # Local sentence-transformers and the other built-in providers keep
        # upstream behaviour: their dimension comes from the model itself.
        return

    litellm_model = model.split("://", 1)[1]
    if litellm_model not in known:
        raise RuntimeError(
            f"{EMBEDDING_DIMENSION_ENV} is not set and {litellm_model!r} is not "
            "one of chonkie's known LiteLLM embedding models. Chonkie would "
            "probe the endpoint at startup and, if it is not reachable, quietly "
            "fall back to 1536 dimensions -- every knowledge-base search then "
            f"fails. Set {EMBEDDING_DIMENSION_ENV} to the embedding column size "
            "(for the live stack: 2000) and restart."
        )


def build_embedding_kwargs(
    environ: Mapping[str, str] | None = None,
    *,
    embedding_model: str | None = None,
) -> dict[str, str | int]:
    """Build keyword arguments for Chonkie's embedding provider."""
    environ = os.environ if environ is None else environ

    embedding_kwargs: dict[str, str | int] = {}
    embedding_base_url = resolve_embedding_base_url(environ)
    if _supports_embedding_api_base(embedding_model):
        if embedding_base_url:
            embedding_kwargs["api_base"] = embedding_base_url
        embedding_dimension = resolve_embedding_dimension(environ)
        if embedding_dimension is not None:
            embedding_kwargs["dimension"] = embedding_dimension

    azure_openai_endpoint = _clean_env_value(environ.get("AZURE_OPENAI_ENDPOINT"))
    azure_openai_api_key = _clean_env_value(environ.get("AZURE_OPENAI_API_KEY"))

    if azure_openai_endpoint:
        embedding_kwargs["azure_endpoint"] = azure_openai_endpoint
    if azure_openai_api_key:
        embedding_kwargs["azure_api_key"] = azure_openai_api_key

    return embedding_kwargs
