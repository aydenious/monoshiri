"""Request and response shapes for saving a URL as a document."""

from __future__ import annotations

from pydantic import BaseModel


class UrlDocumentCreate(BaseModel):
    workspace_id: int
    # Deliberately a string, not ``HttpUrl``: the route refuses a bad scheme
    # or an unreachable host with a declared 400, while a schema rejection
    # would be FastAPI's 422 list — a shape a generated client cannot decode
    # alongside this route's string ``detail``.
    url: str
    folder_id: int | None = None


class UrlDocumentCreated(BaseModel):
    document_id: int


class UrlRejected(BaseModel):
    """The body of a refused URL (400): a fixed string, never exception text."""

    detail: str
