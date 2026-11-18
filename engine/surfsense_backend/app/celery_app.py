"""Celery application configuration and setup."""

import contextlib
import logging
import time

from celery import Celery
from celery.schedules import crontab
from celery.signals import (
    before_task_publish,
    task_postrun,
    task_prerun,
    worker_process_init,
)
from dotenv import load_dotenv

try:
    from opentelemetry import trace
except ImportError:  # pragma: no cover - optional OTel dependency
    trace = None  # type: ignore[assignment]

from app.config import config

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


@before_task_publish.connect
def _stamp_enqueue_time(headers=None, **_kwargs):
    """Stamp enqueue time so workers can measure queue wait."""
    if headers is None:
        return
    with contextlib.suppress(Exception):
        headers["surfsense.enqueued_at_ns"] = str(time.monotonic_ns())


@task_prerun.connect
def _record_queue_latency(task=None, **_kwargs):
    """Record queue latency and stash task metadata for span enrichment."""
    if task is None:
        return
    try:
        from app.observability.domains.celery import (
            parse_celery_task_label,
            record_celery_queue_latency,
        )

        task_name = getattr(task, "name", None) or "unknown"
        operation = parse_celery_task_label(task_name)
        request = getattr(task, "request", None)
        delivery_info = getattr(request, "delivery_info", None) or {}
        queue = delivery_info.get("routing_key") or "unknown"
        scheduled = bool(
            getattr(request, "eta", None) or getattr(request, "expires", None)
        )

        with contextlib.suppress(Exception):
            request.surfsense_operation = operation
            request.surfsense_queue = queue
            request.surfsense_scheduled = scheduled

        headers = getattr(request, "headers", None) or {}
        enqueued_ns = headers.get("surfsense.enqueued_at_ns")
        if enqueued_ns is None:
            return

        elapsed_s = (time.monotonic_ns() - int(enqueued_ns)) / 1e9
        with contextlib.suppress(Exception):
            request.surfsense_queue_latency_ms = elapsed_s * 1000

        record_celery_queue_latency(
            elapsed_s,
            task_name=task_name,
            queue=queue,
            scheduled=scheduled,
            operation=operation,
        )
    except Exception:
        logger.warning("celery_app: could not record the queue latency", exc_info=True)


@task_postrun.connect
def _set_celery_span_attributes(task=None, **_kwargs):
    """Attach derived queue metadata to the active Celery run span."""
    if task is None or trace is None:
        return

    try:
        request = getattr(task, "request", None)
        if request is None:
            return

        span = trace.get_current_span()

        operation = getattr(request, "surfsense_operation", None)
        if operation:
            span.set_attribute("celery.task.operation", operation)

        latency_ms = getattr(request, "surfsense_queue_latency_ms", None)
        if latency_ms is not None:
            span.set_attribute("celery.queue.latency_ms", latency_ms)
    except Exception:
        logger.warning(
            "celery_app: could not set the task span attributes", exc_info=True
        )


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialize the LLM Router and Image Gen Router when a Celery worker process starts.

    This ensures the Auto mode (LiteLLM Router) is available for background tasks
    like agent workflows and image generation.
    """
    from app.observability.setup.lifecycle import init_otel

    init_otel(app=None, traces=True, metrics=True, logs=True)

    from app.config import (
        initialize_image_gen_router,
        initialize_llm_router,
        initialize_openrouter_integration,
        initialize_pricing_registration,
    )

    initialize_openrouter_integration()
    initialize_pricing_registration()
    initialize_llm_router()
    initialize_image_gen_router()


# Celery configuration, sourced from the central Config singleton
CELERY_BROKER_URL = config.CELERY_BROKER_URL
CELERY_RESULT_BACKEND = config.CELERY_RESULT_BACKEND
CELERY_TASK_DEFAULT_QUEUE = config.CELERY_TASK_DEFAULT_QUEUE

# Create Celery app
celery_app = Celery(
    "surfsense",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "app.account_deletion.erase",
        "app.tasks.celery_tasks.document_tasks",
        "app.tasks.celery_tasks.conversion_tasks",
        "app.tasks.celery_tasks.url_tasks",
        "app.podcasts.tasks.draft",
        "app.podcasts.tasks.render",
        "app.tasks.celery_tasks.video_presentation_tasks",
        "app.tasks.celery_tasks.document_reindex_tasks",
        "app.tasks.celery_tasks.stale_notification_cleanup_task",
        "app.tasks.celery_tasks.refresh_token_cleanup_task",
        "app.tasks.celery_tasks.knowledge_store.janitor_task",
        "app.tasks.celery_tasks.knowledge_store.index_tasks",
        "app.tasks.celery_tasks.knowledge_store.drift_monitor_task",
        "app.tasks.celery_tasks.knowledge_store.push_task",
        "app.tasks.celery_tasks.model_compatibility_task",
        "app.tasks.celery_tasks.deliverable_job_tasks",
        "app.etl_pipeline.cache.eviction.task",
        "app.indexing_pipeline.cache.eviction.task",
        "app.automations.tasks.execute_run",
        "app.automations.triggers.builtin.schedule.selector",
        "app.automations.triggers.builtin.event.selector",
    ],
)

# ── Queue names ──────────────────────────────────────────────
# Default queue  : fast, user-facing tasks (file upload, podcast, reindex, …)
# Connectors queue: slow, long-running indexing tasks (Notion, Gmail, web crawl, …)
CONNECTORS_QUEUE = f"{CELERY_TASK_DEFAULT_QUEUE}.connectors"

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=CELERY_TASK_DEFAULT_QUEUE,
    task_default_exchange=CELERY_TASK_DEFAULT_QUEUE,
    task_default_routing_key=CELERY_TASK_DEFAULT_QUEUE,
    # Task execution settings
    task_track_started=True,
    task_time_limit=28800,  # 8 hour hard limit
    task_soft_time_limit=28200,  # 7 hours 50 minutes soft limit
    # VISIBILITY_PATCH (ported from prod): Redis broker's default
    # visibility_timeout is 3600s (1h). A doc that takes >1h to embed on the iGPU
    # (e.g. Physical Metallurgy, ~1h) gets its task RE-DELIVERED to another worker
    # mid-run; the redelivery finds the staged upload file already cleaned up and
    # marks the doc "File not found" / failed. 24h >> any task we expect; the 8h
    # task_time_limit above is the real cap.
    broker_transport_options={"visibility_timeout": 86400},
    # Result backend settings
    result_expires=86400,  # Results expire after 24 hours
    result_extended=True,
    # Worker settings
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    # Retry settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Broker settings
    broker_connection_retry_on_startup=True,
    # Beat scheduler settings
    beat_max_loop_interval=60,  # Check every minute
    # ── Task routing ─────────────────────────────────────────
    # Route slow connector/indexing tasks to a dedicated queue so they
    # never block fast user-facing tasks (file uploads, podcasts, etc.)
    task_routes={
        # A whole-workspace rebuild embeds every document; the per-save index
        # task stays on the fast queue because search freshness is user-facing.
        "reindex_knowledge_store": {"queue": CONNECTORS_QUEUE},
        # Everything else (document processing, podcasts, reindexing,
        # schedule checker, cleanup) stays on the default fast queue.
    },
)

# Imported late (after celery_app is built) to keep the automations triggers
# package out of this module's top-level import graph.
from app.automations.triggers.builtin.schedule.source import (  # noqa: E402
    BEAT_SCHEDULE as SCHEDULE_BEAT_SCHEDULE,
)

# Configure Celery Beat schedule
# This uses a meta-scheduler pattern: instead of creating individual Beat schedules
# for each connector, we have ONE schedule that checks the database at the configured interval
# for connectors that need indexing. This provides dynamic scheduling without restarts.
celery_app.conf.beat_schedule = {
    # Cleanup stale connector indexing notifications every 5 minutes
    # This detects tasks that crashed or timed out without proper cleanup
    # and marks their notifications as failed so users don't see perpetual "syncing"
    "cleanup-stale-indexing-notifications": {
        "task": "cleanup_stale_indexing_notifications",
        "schedule": crontab(minute="*/5"),  # Every 5 minutes
        "options": {
            "expires": 60,  # Task expires after 60 seconds if not picked up
        },
    },
    "reconcile-queued-deliverables": {
        "task": "deliverables.reconcile_queued",
        "schedule": crontab(minute="*"),
        "options": {"expires": 55},
    },
    "purge-refresh-tokens": {
        "task": "purge_refresh_tokens",
        "schedule": crontab(hour="3", minute="41"),
        "options": {"expires": 600},
    },
    # Durable resume: re-queue conversions left unfinished by a worker crash,
    # a host hibernation or a container restart.
    "resume-conversions": {
        "task": "resume_conversions",
        "schedule": crontab(minute="*/5"),
        "options": {"expires": 240},
    },
    # Prune the ETL parse cache (TTL + size budget) once daily, off-peak.
    "evict-etl-cache": {
        "task": "evict_etl_cache",
        "schedule": crontab(hour="4", minute="0"),
        "options": {"expires": 600},
    },
    # Prune the embedding cache (chunk+embedding sets) once daily, off-peak.
    "evict-embedding-cache": {
        "task": "evict_embedding_cache",
        "schedule": crontab(hour="4", minute="30"),
        "options": {"expires": 600},
    },
    # Prune knowledge-store working copies abandoned by crashed threads.
    "prune-knowledge-store-working-copies": {
        "task": "prune_knowledge_store_working_copies",
        "schedule": crontab(hour="4", minute="45"),
        "options": {"expires": 600},
    },
    # Re-drive flipped workspaces whose chunk index trails their store. Hourly,
    # not daily: it is the only recovery for an index task lost to a broker or
    # worker failure, and it backfills a workspace flipped before its first
    # index run.
    "reindex-drifted-workspaces": {
        "task": "reindex_drifted_workspaces",
        "schedule": crontab(minute="20"),
        "options": {"expires": 600},
    },
    # Parity-check flipped workspaces against git by content address. Covers the
    # half the hourly sweep cannot see (its predicate is git-vs-git), and enqueues
    # a whole-tree converge for what it finds, capped per run.
    "check-knowledge-store-drift": {
        "task": "check_knowledge_store_drift",
        "schedule": crontab(hour="5", minute="15"),
        "options": {"expires": 600},
    },
    # Re-probe catalogue models that pass our metadata filters but may no
    # longer serve a turn. Weekly and off-peak: it makes one live call per
    # stale model, and the blocklist it writes is read by every worker's
    # next catalogue refresh.
    "sweep-model-compatibility": {
        "task": "sweep_model_compatibility",
        "schedule": crontab(day_of_week="0", hour="6", minute="0"),
        "options": {"expires": 3600},
    },
    # Fire due automation schedule triggers (Beat entry owned by the schedule
    # trigger; see app.automations.triggers.builtin.schedule.source).
    **SCHEDULE_BEAT_SCHEDULE,
}
