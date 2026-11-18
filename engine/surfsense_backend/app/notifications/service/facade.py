"""Single entry point that composes the per-type notification handlers."""

from __future__ import annotations

import logging

from app.notifications.service.handlers import (
    CommentReplyNotificationHandler,
    DocumentProcessingNotificationHandler,
    MentionNotificationHandler,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Facade over the per-type handlers; mutations sync via Zero."""

    document_processing = DocumentProcessingNotificationHandler()
    mention = MentionNotificationHandler()
    comment_reply = CommentReplyNotificationHandler()
