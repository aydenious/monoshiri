"""``youtube.comments`` capability registration."""

from __future__ import annotations

from app.capabilities.core import (
    ActivityDescriptor,
    Capability,
    register_capability,
)
from app.capabilities.youtube.comments.executor import build_comments_executor
from app.capabilities.youtube.comments.schemas import CommentsInput, CommentsOutput

YOUTUBE_COMMENTS = Capability(
    name="youtube.comments",
    description=(
        "Fetch public YouTube comments and replies with authors, text, likes, "
        "and timestamps. Use video URLs."
    ),
    input_schema=CommentsInput,
    output_schema=CommentsOutput,
    executor=build_comments_executor(),
    docs_url="/docs/legacy/connectors/native/youtube",
    activity=ActivityDescriptor(
        active_title="Reviewing YouTube comments",
        completed_title="Reviewed YouTube comments",
        category="research",
        icon_key="search",
        integration_key="youtube",
    ),
)

register_capability(YOUTUBE_COMMENTS)
