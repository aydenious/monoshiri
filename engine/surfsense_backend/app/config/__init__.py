import copy
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path

import yaml
from chonkie import (  # CHUNKER_PATCH (ported from prod)
    AutoEmbeddings,
    CodeChunker,
    LiteLLMEmbeddings,
    RecursiveChunker,
    RecursiveLevel,
    RecursiveRules,
)
from dotenv import load_dotenv
from rerankers import Reranker

from app.config.embedding_settings import (
    build_embedding_kwargs,
    check_embedding_dimension_config,
    resolve_embedding_base_url,
    resolve_embedding_dimension,
)

# Get the base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env_file = BASE_DIR / ".env"
load_dotenv(env_file)

logger = logging.getLogger(__name__)


os.environ.setdefault("OR_APP_NAME", "SurfSense")
os.environ.setdefault("OR_SITE_URL", "https://surfsense.com")


@lru_cache(maxsize=8)
def _read_global_config_yaml(path_str: str) -> dict:
    """Read and parse ``global_llm_config.yaml`` once per resolved path.

    Cached so the seven ``load_*`` helpers (and their re-invocations during
    startup) don't re-open and re-parse the same file repeatedly. Keyed on the
    resolved path string so tests that monkeypatch ``BASE_DIR`` to a unique
    ``tmp_path`` still get a fresh parse. Callers MUST treat the returned dict
    as read-only and deep-copy any section they intend to mutate.
    """
    f = Path(path_str)
    if not f.exists():
        return {}
    try:
        with open(f, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as e:
        print(f"Warning: Failed to read global_llm_config.yaml: {e}")
        return {}


def _global_config_data() -> dict:
    """Return the parsed global config YAML for the current ``BASE_DIR``.

    ``BASE_DIR`` is read at call time (not bound at import) so a
    ``monkeypatch.setattr(config, "BASE_DIR", tmp_path)`` is honored.
    """
    path = BASE_DIR / "app" / "config" / "global_llm_config.yaml"
    return _read_global_config_yaml(str(path))


def is_ffmpeg_installed():
    """
    Check if ffmpeg is installed on the current system.

    Returns:
        bool: True if ffmpeg is installed, False otherwise.
    """
    return shutil.which("ffmpeg") is not None


def load_global_llm_configs():
    """
    Load global LLM configurations from YAML file.
    Falls back to example file if main file doesn't exist.

    Returns:
        list: List of global LLM config dictionaries, or empty list if file doesn't exist
    """
    data = _global_config_data()
    if not data:
        # No global configs available
        return []

    try:
        # Deep-copy so the in-place mutations below (setdefault, scoring
        # stamps) never leak into the cached YAML structure.
        configs = copy.deepcopy(data.get("global_llm_configs", []))

        # Lazy import keeps the `app.config` -> `app.services` edge one-way.
        from app.services.provider_capabilities import derive_supports_image_input

        seen_slugs: dict[str, int] = {}
        for cfg in configs:
            cfg.setdefault("billing_tier", "free")
            cfg.setdefault("anonymous_enabled", False)
            cfg.setdefault("seo_enabled", False)
            # Capability flag: explicit YAML override always wins. When the
            # operator has not annotated the model, defer to LiteLLM's
            # authoritative model map (`supports_vision`) which already
            # knows GPT-5.x / GPT-4o / Claude 3.x / Gemini 2.x are
            # vision-capable. Unknown / unmapped models default-allow so
            # we don't lock the user out of a freshly added third-party
            # entry; the streaming-task safety net (driven by
            # `is_known_text_only_chat_model`) is the only place a False
            # actually blocks a request.
            if "supports_image_input" not in cfg:
                litellm_params = cfg.get("litellm_params") or {}
                base_model = (
                    litellm_params.get("base_model")
                    if isinstance(litellm_params, dict)
                    else None
                )
                cfg["supports_image_input"] = derive_supports_image_input(
                    provider=cfg.get("provider") or cfg.get("litellm_provider"),
                    model_name=cfg.get("model_name"),
                    base_model=base_model,
                    custom_provider=cfg.get("custom_provider"),
                )

            if cfg.get("seo_enabled") and cfg.get("seo_slug"):
                slug = cfg["seo_slug"]
                if slug in seen_slugs:
                    print(
                        f"Warning: Duplicate seo_slug '{slug}' in global LLM configs "
                        f"(ids {seen_slugs[slug]} and {cfg.get('id')})"
                    )
                else:
                    seen_slugs[slug] = cfg.get("id", 0)

        # Stamp Auto ranking metadata. YAML configs are always
        # Tier A — operator-curated, locked first when premium-eligible.
        # The OpenRouter refresh tick later re-stamps health for any cfg
        # whose provider == "openrouter" via _enrich_health.
        try:
            from app.services.quality_score import static_score_yaml

            for cfg in configs:
                cfg["auto_pin_tier"] = "A"
                static_q = static_score_yaml(cfg)
                cfg["quality_score_static"] = static_q
                cfg["quality_score"] = static_q
                cfg["quality_score_health"] = None
                # YAML cfgs whose provider is openrouter are also subject
                # to health gating against their own /endpoints data — a
                # hand-picked dead OR model is still dead. _enrich_health
                # re-stamps health_gated for them on the next refresh tick.
                cfg["health_gated"] = False
        except Exception:
            logger.exception("Warning: Failed to score global LLM configs")

        # Planner LLM is a singleton role. If an operator accidentally
        # marks multiple configs ``is_planner: true``, only the first one
        # is used at runtime — surface the others at startup so the
        # mistake is caught before traffic, not silently buried.
        planner_cfgs = [c for c in configs if c.get("is_planner") is True]
        if len(planner_cfgs) > 1:
            extra_ids = [c.get("id") for c in planner_cfgs[1:]]
            print(
                "Warning: Multiple global LLM configs marked is_planner=true "
                f"(ids {[c.get('id') for c in planner_cfgs]}); using id "
                f"{planner_cfgs[0].get('id')} and ignoring {extra_ids}"
            )

        return configs
    except Exception:
        logger.exception("Warning: Failed to load global LLM configs")
        return []


def _router_settings(section: str, label: str) -> dict:
    """Defaults merged with a YAML section; the defaults if it is unusable."""
    default_settings = {
        "routing_strategy": "usage-based-routing",
        "num_retries": 3,
        "allowed_fails": 3,
        "cooldown_time": 60,
    }
    try:
        return {**default_settings, **_global_config_data().get(section, {})}
    except (AttributeError, TypeError) as e:
        print(f"Warning: Failed to load {label}: {e}")
        return default_settings


def load_router_settings():
    """
    Load router settings for Auto mode from YAML file.
    Falls back to default settings if not found.

    Returns:
        dict: Router settings dictionary
    """
    return _router_settings("router_settings", "router settings")


def load_global_image_gen_configs():
    """
    Load global image generation configurations from YAML file.

    Returns:
        list: List of global image generation config dictionaries, or empty list
    """
    data = _global_config_data()
    if not data:
        return []

    try:
        configs = copy.deepcopy(data.get("global_image_generation_configs", []) or [])
        for cfg in configs:
            if isinstance(cfg, dict):
                cfg.setdefault("billing_tier", "free")
        return configs
    except (AttributeError, TypeError) as e:
        print(f"Warning: Failed to load global image generation configs: {e}")
        return []


def load_image_gen_router_settings():
    """
    Load router settings for image generation Auto mode from YAML file.

    Returns:
        dict: Router settings dictionary
    """
    return _router_settings(
        "image_generation_router_settings", "image generation router settings"
    )


def load_openrouter_integration_settings() -> dict | None:
    """
    Load OpenRouter integration settings from the YAML config.

    Emits startup warnings for deprecated keys (``billing_tier``,
    ``anonymous_enabled``) and seeds their replacements for back-compat.

    Returns:
        dict with settings if present and enabled, None otherwise
    """
    data = _global_config_data()
    if not data:
        return None

    try:
        # Deep-copy so the setdefault back-compat seeding below never mutates
        # the cached YAML structure.
        settings = copy.deepcopy(data.get("openrouter_integration"))
        if not settings or not settings.get("enabled"):
            return None

        if "billing_tier" in settings:
            print(
                "Warning: openrouter_integration.billing_tier is deprecated; "
                "tier is now derived per model from OpenRouter data "
                "(':free' suffix or zero pricing). Remove this key."
            )

        if "anonymous_enabled" in settings:
            print(
                "Warning: openrouter_integration.anonymous_enabled is "
                "deprecated; use anonymous_enabled_paid and/or "
                "anonymous_enabled_free instead. Both new flags have been "
                "seeded from the legacy value for back-compat."
            )
            settings.setdefault("anonymous_enabled_paid", settings["anonymous_enabled"])
            settings.setdefault("anonymous_enabled_free", settings["anonymous_enabled"])

        # Image generation + vision LLM emission are opt-in (issue L).
        # OpenRouter's catalogue contains hundreds of image / vision
        # capable models; auto-injecting all of them into every
        # deployment would explode the model selector and surprise
        # operators upgrading from prior versions. Default to False so
        # admins must explicitly turn them on.
        settings.setdefault("image_generation_enabled", False)
        settings.setdefault("vision_enabled", False)

        return settings
    except (AttributeError, TypeError) as e:
        print(f"Warning: Failed to load OpenRouter integration settings: {e}")
        return None


def initialize_openrouter_integration():
    """
    If enabled, fetch all OpenRouter models and append them to
    config.GLOBAL_LLM_CONFIGS as dynamic entries. Each model's ``billing_tier``
    is derived per-model from OpenRouter's API signals (``:free`` suffix or
    zero pricing), so free OpenRouter models correctly skip premium quota.

    Should be called BEFORE initialize_llm_router(). Dynamic entries are
    tagged ``router_pool_eligible=False`` so the LiteLLM Router pool (used
    by title-gen / sub-agent flows) remains scoped to curated YAML configs,
    while user-facing Auto-mode thread pinning still considers them.
    """
    settings = load_openrouter_integration_settings()
    if not settings:
        return

    try:
        from app.services.openrouter_integration_service import (
            OpenRouterIntegrationService,
        )

        service = OpenRouterIntegrationService.get_instance()
        new_configs = service.initialize(settings)

        if new_configs:
            config.GLOBAL_LLM_CONFIGS.extend(new_configs)
            free_count = sum(1 for c in new_configs if c.get("billing_tier") == "free")
            premium_count = sum(
                1 for c in new_configs if c.get("billing_tier") == "premium"
            )
            print(
                f"Info: OpenRouter integration added {len(new_configs)} models "
                f"(free={free_count}, premium={premium_count})"
            )
        else:
            print("Info: OpenRouter integration enabled but no models fetched")

        # Image generation emissions reuse the catalogue already cached by
        # ``service.initialize``
        # so we don't make additional network calls here.
        if settings.get("image_generation_enabled"):
            try:
                image_configs = service.get_image_generation_configs()
                if image_configs:
                    config.GLOBAL_IMAGE_GEN_CONFIGS.extend(image_configs)
                    print(
                        f"Info: OpenRouter integration added {len(image_configs)} "
                        f"image-generation models"
                    )
            except Exception:
                logger.exception(
                    "Warning: Failed to inject OpenRouter image-gen configs"
                )

        refresh_global_model_catalog()
    except Exception:
        logger.exception("Warning: Failed to initialize OpenRouter integration")


def materialize_global_configs():
    from app.services.global_model_catalog import materialize_global_model_catalog

    return materialize_global_model_catalog(
        chat_configs=getattr(config, "GLOBAL_LLM_CONFIGS", []),
        image_configs=getattr(config, "GLOBAL_IMAGE_GEN_CONFIGS", []),
    )


def refresh_global_model_catalog():
    connections, models = materialize_global_configs()
    config.GLOBAL_CONNECTIONS = connections
    config.GLOBAL_MODELS = models


def initialize_pricing_registration():
    """
    Teach LiteLLM the per-token cost of every deployment in
    ``config.GLOBAL_LLM_CONFIGS`` (OpenRouter dynamic models pulled
    from the OpenRouter catalogue + any operator-declared YAML pricing).

    Must run AFTER ``initialize_openrouter_integration()`` so the
    OpenRouter catalogue is populated and BEFORE the first LLM call so
    ``response_cost`` is available in ``TokenTrackingCallback``.

    Failures are logged but never raised — startup must not be blocked
    by a missing pricing entry; the worst-case is the model debits 0.
    """
    try:
        from app.services.pricing_registration import (
            register_pricing_from_global_configs,
        )

        register_pricing_from_global_configs()
    except Exception:
        logger.exception("Warning: Failed to register LiteLLM pricing")


def initialize_llm_router():
    """
    Initialize the LLM Router service for Auto mode.
    This should be called during application startup, AFTER
    initialize_openrouter_integration() so dynamic models are included.
    Uses config.GLOBAL_LLM_CONFIGS (in-memory) which includes both
    static YAML configs and dynamic OpenRouter models.
    """
    all_configs = config.GLOBAL_LLM_CONFIGS
    # Reuse the router settings already parsed at Config construction instead
    # of re-reading the YAML here.
    router_settings = config.ROUTER_SETTINGS

    if not all_configs:
        print(
            "Info: No global LLM configs found; global Auto pool is unavailable. "
            "Auto can still use enabled BYOK models."
        )
        return

    try:
        from app.services.llm_router_service import LLMRouterService

        LLMRouterService.initialize(all_configs, router_settings)
        print(
            f"Info: LLM Router initialized with {len(all_configs)} models "
            f"(strategy: {router_settings.get('routing_strategy', 'usage-based-routing')})"
        )
    except Exception:
        logger.exception("Warning: Failed to initialize LLM Router")


def initialize_image_gen_router():
    """
    Initialize the Image Generation Router service for Auto mode.
    This should be called during application startup.
    """
    image_gen_configs = load_global_image_gen_configs()
    # Reuse the router settings already parsed at Config construction. The
    # *configs* list is intentionally re-read from YAML (it must exclude the
    # OpenRouter-injected dynamic models held in config.GLOBAL_IMAGE_GEN_CONFIGS).
    router_settings = config.IMAGE_GEN_ROUTER_SETTINGS

    if not image_gen_configs:
        print(
            "Info: No global image generation configs found, "
            "Image Generation Auto mode will not be available"
        )
        return

    try:
        from app.services.image_gen_router_service import ImageGenRouterService

        ImageGenRouterService.initialize(image_gen_configs, router_settings)
        print(
            f"Info: Image Generation Router initialized with {len(image_gen_configs)} models "
            f"(strategy: {router_settings.get('routing_strategy', 'usage-based-routing')})"
        )
    except Exception:
        logger.exception("Warning: Failed to initialize Image Generation Router")


class Config:
    # Check if ffmpeg is installed
    if not is_ffmpeg_installed():
        allow_static_ffmpeg = (
            os.getenv("SURFSENSE_ALLOW_STATIC_FFMPEG_DOWNLOAD", "TRUE").upper()
            == "TRUE"
        )
        if allow_static_ffmpeg:
            import static_ffmpeg

            # ffmpeg installed on first call to add_paths(), threadsafe.
            static_ffmpeg.add_paths()

        # check if ffmpeg is installed again
        if not is_ffmpeg_installed():
            raise ValueError(
                "FFmpeg is not installed on the system. Please install it to use the Surfsense Podcaster."
            )

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL")

    # When TRUE (default) the app ensures extensions/tables/indexes exist on
    # startup. Set FALSE in environments where schema is owned exclusively by
    # Alembic migrations to skip all boot-time DDL.
    DB_BOOTSTRAP_ON_STARTUP = (
        os.getenv("DB_BOOTSTRAP_ON_STARTUP", "TRUE").upper() == "TRUE"
    )
    # Per-session lock_timeout (ms) applied to boot-time DDL so a contended
    # CREATE INDEX / CREATE TABLE fails fast instead of hanging the FastAPI
    # lifespan forever behind another transaction's lock.
    DB_DDL_LOCK_TIMEOUT_MS = int(os.getenv("DB_DDL_LOCK_TIMEOUT_MS", "5000"))
    # Global idle_in_transaction_session_timeout (ms) applied to every pooled
    # connection so an abandoned "idle in transaction" session can't wedge the
    # database indefinitely. 0 disables. Only applied to asyncpg connections.
    DB_IDLE_IN_TX_TIMEOUT_MS = int(os.getenv("DB_IDLE_IN_TX_TIMEOUT_MS", "900000"))
    # Same protection for the separate Celery worker engine, where long-running
    # ingestion/podcast/video tasks live. Kept higher than the web default so a
    # legitimate per-document embed window is never reaped: if a task hasn't
    # touched the DB in 60 min it's treated as orphaned and dropped. 0 disables.
    DB_CELERY_IDLE_IN_TX_TIMEOUT_MS = int(
        os.getenv("DB_CELERY_IDLE_IN_TX_TIMEOUT_MS", "3600000")
    )

    # Celery / Redis
    # Redis (single endpoint for Celery broker, result backend, and app cache).
    # Legacy CELERY_BROKER_URL / CELERY_RESULT_BACKEND / REDIS_APP_URL still
    # override individually when you need to split Redis across instances.
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
    CELERY_TASK_DEFAULT_QUEUE = os.getenv("CELERY_TASK_DEFAULT_QUEUE", "surfsense")
    REDIS_APP_URL = os.getenv("REDIS_APP_URL", CELERY_BROKER_URL)

    # File storage (local filesystem by default; Azure Blob optional)
    FILE_STORAGE_BACKEND = os.getenv("FILE_STORAGE_BACKEND", "local").strip().lower()
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_STORAGE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER")
    FILE_STORAGE_LOCAL_PATH = os.getenv(
        "FILE_STORAGE_LOCAL_PATH", str(BASE_DIR / ".local_object_store")
    )

    # Knowledge store (Git-native KB; off by default). Nested under the shared
    # file-storage volume so every process sees the same history.
    KNOWLEDGE_STORE_ENABLED = (
        os.getenv("KNOWLEDGE_STORE_ENABLED", "FALSE").upper() == "TRUE"
    )
    KNOWLEDGE_STORE_ROOT = os.getenv(
        "KNOWLEDGE_STORE_ROOT",
        os.path.join(FILE_STORAGE_LOCAL_PATH, "knowledge_store"),
    )
    # GitHub App used to push a git-native workspace to the user's GitHub repo.
    GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
    GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
    GITHUB_APP_SLUG = os.getenv("GITHUB_APP_SLUG", "")
    # User-to-server OAuth on the same App: lets a signed-in user enumerate
    # their own installations so connect/reconnect never depends on the
    # global Setup-URL callback. Client ID/secret live on the App's General tab.
    GITHUB_APP_CLIENT_ID = os.getenv("GITHUB_APP_CLIENT_ID", "")
    GITHUB_APP_CLIENT_SECRET = os.getenv("GITHUB_APP_CLIENT_SECRET", "")

    # Daytona sandbox (code execution / filesystem sandbox)
    DAYTONA_API_KEY = os.getenv("DAYTONA_API_KEY", "")
    DAYTONA_API_URL = os.getenv("DAYTONA_API_URL", "https://app.daytona.io/api")
    DAYTONA_TARGET = os.getenv("DAYTONA_TARGET", "us")
    DAYTONA_SNAPSHOT_ID = os.getenv("DAYTONA_SNAPSHOT_ID") or None
    SANDBOX_FILES_DIR = os.getenv("SANDBOX_FILES_DIR", "sandbox_files")

    # Sandbox provider selection. A deployment choice, not a fallback chain:
    # opensandbox is self-hosted, daytona is cloud.
    #
    # On by default because compose always runs the control plane. A backend run
    # outside compose has none, so it needs OPENSANDBOX_DOMAIN pointed at a
    # reachable server or SANDBOX_ENABLED=FALSE.
    SANDBOX_ENABLED = os.getenv("SANDBOX_ENABLED", "TRUE").strip().upper() == "TRUE"
    SANDBOX_PROVIDER = os.getenv("SANDBOX_PROVIDER", "opensandbox").lower()
    OPENSANDBOX_DOMAIN = os.getenv("OPENSANDBOX_DOMAIN", "opensandbox-server:8080")
    OPENSANDBOX_API_KEY = os.getenv("OPENSANDBOX_API_KEY", "")
    SANDBOX_IMAGE = os.getenv(
        "SANDBOX_IMAGE", "ghcr.io/modsetter/surfsense-sandbox:latest"
    )
    SANDBOX_IDLE_TTL_SECONDS = int(os.getenv("SANDBOX_IDLE_TTL_SECONDS", "900"))
    # Creation blocks server-side while the image is pulled onto the host
    # daemon, which the SDK's 30s default turns into an error rather than a slow
    # first request. Compose pre-pulls; this covers hosts that did not.
    SANDBOX_OPERATION_TIMEOUT_SECONDS = int(
        os.getenv("SANDBOX_OPERATION_TIMEOUT_SECONDS", "200")
    )
    SANDBOX_MAX_SESSIONS_PER_WORKSPACE = int(
        os.getenv("SANDBOX_MAX_SESSIONS_PER_WORKSPACE", "5")
    )
    VIDEO_SANDBOX_RENDERING_ENABLED = (
        os.getenv("VIDEO_SANDBOX_RENDERING_ENABLED", "FALSE").strip().upper() == "TRUE"
    )
    # Per API worker in v1; fleet capacity is workers x this value.
    VIDEO_SANDBOX_MAX_CONCURRENT_RENDERS = int(
        os.getenv("VIDEO_SANDBOX_MAX_CONCURRENT_RENDERS", "1")
    )
    VIDEO_SANDBOX_MAX_FRAMES_PER_SEGMENT = int(
        os.getenv("VIDEO_SANDBOX_MAX_FRAMES_PER_SEGMENT", "1800")
    )
    VIDEO_SANDBOX_RENDER_FRAME_TIMEOUT_MS = int(
        os.getenv("VIDEO_SANDBOX_RENDER_FRAME_TIMEOUT_MS", "7000")
    )
    ARTIFACT_MAX_FILE_BYTES = int(os.getenv("ARTIFACT_MAX_FILE_BYTES", "31457280"))

    # Agent cache (in-process LRU+TTL cache for built agents)
    AGENT_CACHE_MAXSIZE = int(os.getenv("SURFSENSE_AGENT_CACHE_MAXSIZE", "256"))
    AGENT_CACHE_TTL_SECONDS = float(
        os.getenv("SURFSENSE_AGENT_CACHE_TTL_SECONDS", "1800")
    )

    # Connector discovery cache TTL
    CONNECTOR_DISCOVERY_TTL_SECONDS = float(
        os.getenv("SURFSENSE_CONNECTOR_DISCOVERY_TTL_SECONDS", "30")
    )

    SURFSENSE_PUBLIC_URL = os.getenv("SURFSENSE_PUBLIC_URL")
    NEXT_FRONTEND_URL = os.getenv("NEXT_FRONTEND_URL") or SURFSENSE_PUBLIC_URL
    # Backend URL to override the http to https in the OAuth redirect URI
    BACKEND_URL = os.getenv("BACKEND_URL") or SURFSENSE_PUBLIC_URL

    # Default for the ``users.credit_micros_balance`` column, in micro-USD.
    # Kept because the historical alembic revision 186 reads it for
    # ``SET DEFAULT``; nothing in the running app reads a balance any more.
    DEFAULT_CREDIT_MICROS_BALANCE = int(
        os.getenv("DEFAULT_CREDIT_MICROS_BALANCE", "5000000")
    )

    # Auth
    AUTH_TYPE = os.getenv("AUTH_TYPE", "LOCAL")
    REGISTRATION_ENABLED = os.getenv("REGISTRATION_ENABLED", "TRUE").upper() == "TRUE"
    # Monoshiri: signup is invite-only unless SIGNUP_OPEN=TRUE (break-glass).
    SIGNUP_OPEN = os.getenv("SIGNUP_OPEN", "FALSE").upper() == "TRUE"
    INVITE_TTL_SECONDS = int(os.getenv("INVITE_TTL_SECONDS", str(7 * 24 * 60 * 60)))
    # Max workspaces a user may own. The frontend reads this through the
    # workspace limits route; do not duplicate this value client-side.
    MAX_WORKSPACES_PER_USER = int(os.getenv("MAX_WORKSPACES_PER_USER", "400"))

    # Google OAuth
    GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

    # LLM instances are now managed per-user through the LLMConfig system
    # Legacy environment variables removed in favor of user-specific configurations

    # True when an operator-provided global_llm_config.yaml is present.
    # Used to gate the per-workspace LLM onboarding flow: when a global
    # config file exists, workspaces inherit it and onboarding is skipped.
    GLOBAL_LLM_CONFIG_FILE_EXISTS = (
        BASE_DIR / "app" / "config" / "global_llm_config.yaml"
    ).exists()

    # Global LLM Configurations (optional)
    # Load from global_llm_config.yaml if available
    # These can be used as default options for users
    GLOBAL_LLM_CONFIGS = load_global_llm_configs()

    # Router settings for Auto mode (LiteLLM Router load balancing)
    ROUTER_SETTINGS = load_router_settings()

    # Global Image Generation Configurations (optional)
    GLOBAL_IMAGE_GEN_CONFIGS = load_global_image_gen_configs()

    # Router settings for Image Generation Auto mode
    IMAGE_GEN_ROUTER_SETTINGS = load_image_gen_router_settings()

    # Virtual GLOBAL connection/model catalog. This is server-only metadata
    # derived from global_llm_config.yaml; GLOBAL keys are not stored in DB.
    from app.services.global_model_catalog import (
        materialize_global_model_catalog as _materialize_global_model_catalog,
    )

    GLOBAL_CONNECTIONS, GLOBAL_MODELS = _materialize_global_model_catalog(
        chat_configs=GLOBAL_LLM_CONFIGS,
        image_configs=GLOBAL_IMAGE_GEN_CONFIGS,
    )
    del _materialize_global_model_catalog

    # OpenRouter Integration settings (optional)
    OPENROUTER_INTEGRATION_SETTINGS = load_openrouter_integration_settings()

    # Chonkie Configuration | Edit this to your needs
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
    EMBEDDING_BASE_URL = resolve_embedding_base_url()
    EMBEDDING_DIMENSION = resolve_embedding_dimension()
    # Azure OpenAI credentials from environment variables
    AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")

    # Pass provider-specific settings to embeddings when supported.
    embedding_kwargs = build_embedding_kwargs(embedding_model=EMBEDDING_MODEL)
    # EMBED_TIMEOUT_PATCH: chonkie's LiteLLMEmbeddings defaults to timeout=60s /
    # max_retries=3, and embed_batch() sends a whole document's chunks in ONE
    # litellm.embedding() call. On the iGPU that batch runs well past 60s → it
    # times out and the 3 retries re-send the full batch, stampeding the 2-slot
    # embedder until the doc fails. Give it a generous timeout so the batch
    # completes in one shot (must be <= embed-proxy UPSTREAM_TIMEOUT).
    # Remote (provider://model) embedders only: local SentenceTransformer models
    # reject these kwargs (Monoshiri fix; the live stack only ever ran remote).
    if "://" in (EMBEDDING_MODEL or ""):
        embedding_kwargs.setdefault("timeout", 1800)
        embedding_kwargs.setdefault("max_retries", 2)

    # EMBEDDING_DIMENSION_PIN: incident 2026-11-08. Chonkie's LiteLLMEmbeddings
    # probes the endpoint ("test") at construction when it neither gets a
    # dimension nor knows the model; if the probe fails it silently returns
    # 1536. After a hibernation the backend booted before embed-proxy answered,
    # built Vector(1536) columns, and every KB search failed with "expected 1536
    # dimensions, not 2000". Check BEFORE constructing the instance so no probe
    # is made: litellm:// models that chonkie does not know require
    # EMBEDDING_DIMENSION. Local/other providers keep upstream behaviour.
    check_embedding_dimension_config(
        EMBEDDING_MODEL,
        EMBEDDING_DIMENSION,
        LiteLLMEmbeddings.KNOWN_DIMENSIONS,
    )
    embedding_model_instance = AutoEmbeddings.get_embeddings(
        EMBEDDING_MODEL,
        **embedding_kwargs,
    )
    # EMBEDDING_DIMENSION_PIN (continued): also refuse a provider that ignored
    # the kwarg (or reports its own size) and disagrees with EMBEDDING_DIMENSION.
    check_embedding_dimension_config(
        EMBEDDING_MODEL,
        EMBEDDING_DIMENSION,
        LiteLLMEmbeddings.KNOWN_DIMENSIONS,
        instance_dimension=embedding_model_instance.dimension,
    )
    # EMBEDDING_API_BASE_PIN (ported from prod): langchain's ChatLiteLLM sets
    # `litellm.api_base` GLOBALLY on every chat LLM call. The embedder otherwise
    # resolves its base from the OPENAI_API_BASE env, so after any chat turn the
    # global base (e.g. api.deepseek.com) HIJACKS query embeddings -> they hit the
    # chat provider's /embeddings endpoint -> 404, breaking knowledge-base search.
    # Pin the embedder's own api_base so chonkie passes it explicitly and wins.
    _embed_base = os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE")
    if _embed_base:
        try:
            embedding_model_instance.api_base = _embed_base
        except (AttributeError, TypeError, ValueError):
            logger.warning("Could not pin the embedder api_base", exc_info=True)
    is_local_embedding_model = "://" not in (EMBEDDING_MODEL or "")
    # CHUNKER_PATCH start — token-based sizing + markdown-header split (ported
    # from the prod pinned-2026-05-03 stack). Vanilla 0.0.32 sizes chunks by
    # CHARACTERS (chunk_size=512 chars ≈ 90 tokens → ~2148 chunks for a 767 KB
    # doc, weak retrieval). gpt2-tokenized 512-TOKEN chunks (~1230 chars) are the
    # retrieval sweet spot and cut chunk count ~3.7x (matches prod's 19,729
    # total). Level 0 splits on markdown headers so sections stay intact.
    # NOTE: 512 is deliberate — chunk_size=2048 was tested and made the agent's
    # failure mode worse (GraphRecursionError on huge docs). See docs/gotchas.md.
    chunker_instance = RecursiveChunker(
        tokenizer="gpt2",
        chunk_size=512,
        min_characters_per_chunk=80,
        rules=RecursiveRules(
            levels=[
                RecursiveLevel(
                    delimiters=["\n## ", "\n### ", "\n#### "],
                    include_delim="next",
                ),
                RecursiveLevel(
                    delimiters=["\n\n", "\r\n", "\n", "\r"],
                    include_delim="prev",
                ),
                RecursiveLevel(
                    delimiters=[". ", "! ", "? "],
                    include_delim="prev",
                ),
                RecursiveLevel(whitespace=True, include_delim="prev"),
                RecursiveLevel(include_delim="prev"),
            ]
        ),
    )
    # CHUNKER_PATCH end
    code_chunker_instance = CodeChunker(
        chunk_size=getattr(embedding_model_instance, "max_seq_length", 512)
    )

    # Reranker's Configuration | Pinecone, Cohere etc. Read more at https://github.com/AnswerDotAI/rerankers?tab=readme-ov-file#usage
    RERANKERS_ENABLED = os.getenv("RERANKERS_ENABLED", "FALSE").upper() == "TRUE"
    if RERANKERS_ENABLED:
        RERANKERS_MODEL_NAME = os.getenv("RERANKERS_MODEL_NAME")
        RERANKERS_MODEL_TYPE = os.getenv("RERANKERS_MODEL_TYPE")
        # LLAMACPP_RERANKER (Monoshiri): llama.cpp /v1/rerank backend; replaces
        # the live stack's sitecustomize.py import hook (RERANKER_SHIM).
        from app.services import llamacpp_reranker

        if RERANKERS_MODEL_TYPE == llamacpp_reranker.MODEL_TYPE:
            reranker_instance = llamacpp_reranker.LlamacppReranker(
                url=os.getenv("RERANKER_API_URL", llamacpp_reranker.DEFAULT_URL),
                model=RERANKERS_MODEL_NAME,
            )
            print(f"[LLAMACPP_RERANKER] active -> {reranker_instance.url}")
        else:
            reranker_instance = Reranker(
                model_name=RERANKERS_MODEL_NAME,
                model_type=RERANKERS_MODEL_TYPE,
            )
    else:
        reranker_instance = None

    # OAuth JWT
    SECRET_KEY = os.getenv("SECRET_KEY")

    # JWT Token Lifetimes
    ACCESS_TOKEN_LIFETIME_SECONDS = int(
        os.getenv("ACCESS_TOKEN_LIFETIME_SECONDS", str(60 * 60))  # 60 minutes
    )
    MIN_ISSUED_AT = int(os.getenv("MIN_ISSUED_AT", "0"))
    REFRESH_TOKEN_LIFETIME_SECONDS = int(
        os.getenv("REFRESH_TOKEN_LIFETIME_SECONDS", str(14 * 24 * 60 * 60))  # 2 weeks
    )
    REFRESH_ROTATION_GRACE_SECONDS = int(
        os.getenv("REFRESH_ROTATION_GRACE_SECONDS", "45")
    )
    REFRESH_ABSOLUTE_LIFETIME_SECONDS = int(
        os.getenv("REFRESH_ABSOLUTE_LIFETIME_SECONDS", str(30 * 24 * 60 * 60))
    )
    if REFRESH_ABSOLUTE_LIFETIME_SECONDS <= REFRESH_TOKEN_LIFETIME_SECONDS:
        raise ValueError(
            "REFRESH_ABSOLUTE_LIFETIME_SECONDS must be greater than "
            "REFRESH_TOKEN_LIFETIME_SECONDS so the sliding inactivity window works."
        )
    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "surfsense_session")
    REFRESH_COOKIE_NAME = os.getenv("REFRESH_COOKIE_NAME", "surfsense_refresh")
    SESSION_COOKIE_SECURE_POLICY = os.getenv(
        "SESSION_COOKIE_SECURE_POLICY", "auto"
    ).lower()
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
    if SESSION_COOKIE_SAMESITE == "none":
        raise ValueError("SESSION_COOKIE_SAMESITE=none is not supported")
    COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN") or None
    CSRF_ALLOWED_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CSRF_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    _PAT_MAX_EXPIRY_DAYS = os.getenv("PAT_MAX_EXPIRY_DAYS", "").strip()
    PAT_MAX_EXPIRY_DAYS = int(_PAT_MAX_EXPIRY_DAYS) if _PAT_MAX_EXPIRY_DAYS else None

    # ETL Service
    ETL_SERVICE = os.getenv("ETL_SERVICE")

    if ETL_SERVICE == "LLAMACLOUD":
        LLAMA_CLOUD_API_KEY = os.getenv("LLAMA_CLOUD_API_KEY")
        # Optional: Azure Document Intelligence accelerator for supported file types
        AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT")
        AZURE_DI_KEY = os.getenv("AZURE_DI_KEY")

    # MinerU conversion (Phase 3). Unconditional settings: the client, parser and
    # batch loop may be imported even when ETL_SERVICE is DOCLING.
    MINERU_URL = os.getenv("MINERU_URL", "http://mineru-api:8000")
    MINERU_API_KEY = os.getenv("MINERU_API_KEY") or None
    MINERU_TIER = os.getenv("MINERU_TIER", "standard")
    # Pages per MinerU task; a non-PDF input is one batch regardless.
    MINERU_BATCH_PAGES = int(os.getenv("MINERU_BATCH_PAGES", "10"))
    MINERU_POLL_SECONDS = float(os.getenv("MINERU_POLL_SECONDS", "5.0"))
    # One MinerU task may not run longer than this before it is cancelled and
    # re-submitted (then failed on the second expiry): a hung task must not
    # keep a document "working" for hours.
    MINERU_BATCH_TIMEOUT_SECONDS = float(
        os.getenv("MINERU_BATCH_TIMEOUT_SECONDS", "1800")
    )
    # 24 h without progress before a conversion is given up.
    MINERU_GIVE_UP_SECONDS = int(os.getenv("MINERU_GIVE_UP_SECONDS", "86400"))
    # Durable per-document job directory (mounted volume in deployment).
    CONVERSION_WORK_DIR = os.getenv("CONVERSION_WORK_DIR", "/app/conversions")
    CONVERSION_WORK_DIR_MAX_BYTES = int(
        os.getenv("CONVERSION_WORK_DIR_MAX_BYTES", str(20 * 1024**3))
    )

    # ETL parse cache: reuse parser output for identical bytes across workspaces.
    ETL_CACHE_ENABLED = (
        os.getenv("ETL_CACHE_ENABLED", "false").strip().lower() == "true"
    )
    # Bump to invalidate every cached entry after a parser/behaviour change.
    ETL_CACHE_PARSER_VERSION = int(os.getenv("ETL_CACHE_PARSER_VERSION", "1"))
    ETL_CACHE_TTL_DAYS = int(os.getenv("ETL_CACHE_TTL_DAYS", "90"))
    ETL_CACHE_MAX_TOTAL_MB = int(os.getenv("ETL_CACHE_MAX_TOTAL_MB", "5120"))
    ETL_CACHE_EVICTION_BATCH = int(os.getenv("ETL_CACHE_EVICTION_BATCH", "500"))
    # Optional dedicated blob storage; unset reuses the main file_storage backend.
    ETL_CACHE_STORAGE_BACKEND = os.getenv("ETL_CACHE_STORAGE_BACKEND")
    ETL_CACHE_STORAGE_CONTAINER = os.getenv("ETL_CACHE_STORAGE_CONTAINER")
    ETL_CACHE_STORAGE_LOCAL_PATH = os.getenv("ETL_CACHE_STORAGE_LOCAL_PATH")

    # Embedding cache: reuse chunk+embedding output for identical markdown across
    # workspaces. Blobs share the ETL_CACHE_STORAGE_* backend.
    EMBEDDING_CACHE_ENABLED = (
        os.getenv("EMBEDDING_CACHE_ENABLED", "false").strip().lower() == "true"
    )
    # Bump to invalidate every cached embedding set after a chunker change.
    EMBEDDING_CACHE_CHUNKER_VERSION = int(
        os.getenv("EMBEDDING_CACHE_CHUNKER_VERSION", "1")
    )
    EMBEDDING_CACHE_TTL_DAYS = int(os.getenv("EMBEDDING_CACHE_TTL_DAYS", "90"))
    EMBEDDING_CACHE_MAX_TOTAL_MB = int(
        os.getenv("EMBEDDING_CACHE_MAX_TOTAL_MB", "5120")
    )
    EMBEDDING_CACHE_EVICTION_BATCH = int(
        os.getenv("EMBEDDING_CACHE_EVICTION_BATCH", "500")
    )

    # Incremental re-indexing: on document edits, keep chunk rows whose text is
    # unchanged (reusing their embeddings) and embed only new/changed chunks.
    # Kill switch -- disabling falls back to delete-all + full re-embed.
    CHUNK_RECONCILE_ENABLED = (
        os.getenv("CHUNK_RECONCILE_ENABLED", "true").strip().lower() == "true"
    )
    INDEXING_CHUNK_INSERT_BATCH_SIZE = int(
        os.getenv("INDEXING_CHUNK_INSERT_BATCH_SIZE", "200")
    )

    # Proxy provider selection. Maps to a ProxyProvider implementation registered
    # in app/utils/proxy/registry.py. Add new vendors there and switch via this var.
    PROXY_PROVIDER = os.getenv("PROXY_PROVIDER", "custom")

    # Proxy endpoint(s), shared across all providers — PROXY_PROVIDER selects how
    # they're interpreted, not a different env name. PROXY_URL is a single full
    # http://user:pass@host:port endpoint (used by every provider); e.g. DataImpulse  # pragma: allowlist secret
    # encodes country as a "__cr.<country>" username suffix that its provider parses
    # for geoip-match. PROXY_URLS is a comma-separated pool that the "custom" provider
    # rotates client-side (server-side-rotating gateways ignore it). Leave unset to
    # disable proxying.
    PROXY_URL = os.getenv("PROXY_URL")
    PROXY_URLS = os.getenv("PROXY_URLS")

    # =====================================================================
    # Phase 3d — Captcha solving (reCAPTCHA v2/v3, hCaptcha, v2-Enterprise) via
    # the in-house solver seam (app/utils/captcha/solvers.py).
    # The LAST-resort bypass tier: only fires on the StealthyFetcher browser
    # tier, only when a sitekey is detected, and only when explicitly enabled.
    # Cloudflare Turnstile is already handled free in-framework (03a), NOT here.
    # One app-wide config (mirrors the single PROXY_PROVIDER model) — no
    # per-connector config. Off by default => zero solve attempts, zero cost.
    # Solving may violate a target site's ToS; treat as opt-in/owner-acknowledged
    # and public-data only (no logged-in bypass).
    # =====================================================================
    CAPTCHA_SOLVING_ENABLED = (
        os.getenv("CAPTCHA_SOLVING_ENABLED", "FALSE").upper() == "TRUE"
    )
    # Solver vendor. "capsolver" (AI-native, fastest on reCAPTCHA-Enterprise) and
    # "2captcha" have in-house clients today; anticaptcha / capmonster are added
    # progressively in solvers._PROVIDERS.
    CAPTCHA_SOLVER_PROVIDER = os.getenv("CAPTCHA_SOLVER_PROVIDER", "capsolver")
    CAPTCHA_SOLVER_API_KEY = os.getenv("CAPTCHA_SOLVER_API_KEY")
    # Per-URL solve cap so one hostile page can't burn unbounded solver credit.
    CAPTCHA_MAX_ATTEMPTS_PER_URL = int(os.getenv("CAPTCHA_MAX_ATTEMPTS_PER_URL", "1"))
    # Abort a single solve after this many seconds (solves take 10-60s).
    CAPTCHA_SOLVE_TIMEOUT_S = int(os.getenv("CAPTCHA_SOLVE_TIMEOUT_S", "120"))
    # Default captcha type when detection is ambiguous: v2 | v3 | hcaptcha.
    CAPTCHA_TYPE_DEFAULT = os.getenv("CAPTCHA_TYPE_DEFAULT", "v2")
    # reCAPTCHA v3 tuning (only used for v3 challenges).
    CAPTCHA_V3_MIN_SCORE = float(os.getenv("CAPTCHA_V3_MIN_SCORE", "0.7"))
    CAPTCHA_V3_ACTION = os.getenv("CAPTCHA_V3_ACTION", "verify")

    # =====================================================================
    # Phase 3e — Stealth hardening (Slice A): runtime/config-level levers
    # layered on Scrapling's patchright-Chromium StealthyFetcher tier. All are
    # consumed by the centralized kwargs builder in
    # app/proprietary/web_crawler/stealth.py (proprietary — bypass tuning), which
    # is the single source of truth imported by the crawler AND the 03f harness
    # (no test-vs-prod drift). Defaults preserve today's behavior /
    # introduce no crawl-speed regression. See plans/backend/03e-stealth-hardening.md.
    # =====================================================================
    # Map the active proxy provider's exit region (ProxyProvider.get_location())
    # -> browser locale/timezone so the fingerprint coheres with the proxy exit
    # geo. No exit-IP lookup (zero added latency); unknown/empty region => skip.
    CRAWL_GEOIP_MATCH_ENABLED = (
        os.getenv("CRAWL_GEOIP_MATCH_ENABLED", "FALSE").upper() == "TRUE"
    )
    # Force WebRTC to respect the proxy (prevents real-local-IP leak). Cheap +
    # safe => default TRUE.
    CRAWL_BLOCK_WEBRTC = os.getenv("CRAWL_BLOCK_WEBRTC", "TRUE").upper() == "TRUE"
    # Random canvas noise. An UNSTABLE canvas hash is itself a fingerprint tell,
    # so default FALSE (opt-in + 03f-validated). See 03e §2.
    CRAWL_HIDE_CANVAS = os.getenv("CRAWL_HIDE_CANVAS", "FALSE").upper() == "TRUE"
    # Set a Google referer so the first hit looks like organic arrival.
    CRAWL_GOOGLE_SEARCH_REFERER = (
        os.getenv("CRAWL_GOOGLE_SEARCH_REFERER", "TRUE").upper() == "TRUE"
    )
    # Route DNS via Cloudflare DoH (anti DNS-leak behind proxies). Adds a DNS
    # round-trip => default FALSE to honor the "no speed regression" bar; flip on
    # when leak-safety outweighs the marginal latency.
    CRAWL_DNS_OVER_HTTPS = os.getenv("CRAWL_DNS_OVER_HTTPS", "FALSE").upper() == "TRUE"
    # Promises an Xvfb display so the browser can run headful (TikTok's profile
    # feed is empty to headless Chromium). Off keeps every browser headless.
    CRAWL_HEADED_XVFB_ENABLED = (
        os.getenv("CRAWL_HEADED_XVFB_ENABLED", "FALSE").upper() == "TRUE"
    )

    # Litellm TTS Configuration
    TTS_SERVICE = os.getenv("TTS_SERVICE")
    TTS_SERVICE_API_BASE = os.getenv("TTS_SERVICE_API_BASE")
    TTS_SERVICE_API_KEY = os.getenv("TTS_SERVICE_API_KEY")

    # STT Configuration
    STT_SERVICE = os.getenv("STT_SERVICE")
    STT_SERVICE_API_BASE = os.getenv("STT_SERVICE_API_BASE")
    STT_SERVICE_API_KEY = os.getenv("STT_SERVICE_API_KEY")

    # Video presentation defaults
    VIDEO_PRESENTATION_MAX_SLIDES = int(
        os.getenv("VIDEO_PRESENTATION_MAX_SLIDES", "30")
    )
    VIDEO_PRESENTATION_FPS = int(os.getenv("VIDEO_PRESENTATION_FPS", "30"))
    VIDEO_PRESENTATION_DEFAULT_DURATION_IN_FRAMES = int(
        os.getenv("VIDEO_PRESENTATION_DEFAULT_DURATION_IN_FRAMES", "300")
    )
    # BCP-47 narration language used when the slide-generating LLM does not
    # report the language it wrote in. A tag the configured TTS provider cannot
    # speak is ignored in favour of English, so a bad value degrades rather
    # than failing the render.
    VIDEO_PRESENTATION_DEFAULT_LANGUAGE = os.getenv(
        "VIDEO_PRESENTATION_DEFAULT_LANGUAGE", "en"
    )

    # Validation Checks
    # Check embedding dimension
    if (
        hasattr(embedding_model_instance, "dimension")
        and embedding_model_instance.dimension > 2000
    ):
        raise ValueError(
            f"Embedding dimension for Model: {EMBEDDING_MODEL} "
            f"has {embedding_model_instance.dimension} dimensions, which "
            f"exceeds the maximum of 2000 allowed by PGVector."
        )


# Create a config instance
config = Config()
