"""Shared pieces of the streaming SSE protocol.

Layout:
* ``types.py`` - TypedDicts for activity payloads
* ``emitter/`` - identity of the agent that emitted an event + runtime registry

Production chat uses ``app.services.new_streaming_service`` from
``app.tasks.chat.stream_new_chat`` and related routes.
"""

from __future__ import annotations
