"""SurfSense observability.

Process lifecycle is exposed here; telemetry is organized as:
- ``signals`` — generic OTel mechanism (spans, metrics)
- ``domains`` — per-concept spans + metrics (agent, chat, kb, etl, ...)
- ``analytics`` — no-op analytics shim (telemetry removed)
- ``core`` / ``setup`` — vocabulary/policy and install-once wiring
"""

from app.observability.setup.lifecycle import init_otel, shutdown_otel

__all__ = ["init_otel", "shutdown_otel"]
