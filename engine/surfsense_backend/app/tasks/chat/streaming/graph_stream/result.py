"""Mutable facts collected while relaying one agent stream (``stream_output``).

``StreamingResult`` is the orchestrators' ``StreamResult``; ``stream_output``
fills the same object they read afterwards.
"""

from __future__ import annotations

from app.tasks.chat.streaming.shared.stream_result import (
    StreamResult as StreamingResult,
)

__all__ = ["StreamingResult"]
