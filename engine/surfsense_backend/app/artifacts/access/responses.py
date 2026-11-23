"""Typed response bodies for the artifact routes.

These models are the contract the iOS client generates Swift from, so they
mirror the routes' existing dict output field for field. The routes keep
returning the same dicts; FastAPI validates them through these models.
``response_model_exclude_unset=True`` keeps optional keys that are absent
today absent, while keys sent as ``null`` (``updated_at``) stay ``null``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ArtifactLegacyRef(BaseModel):
    """Legacy podcast / video / image reference stashed under ``metadata.legacy``."""

    kind: str
    id: int


class ArtifactListItem(BaseModel):
    artifact_id: int
    document_id: int
    title: str
    format: str
    generation: int
    indexing_status: str
    thread_id: int | None
    created_at: str
    updated_at: str | None
    legacy: ArtifactLegacyRef | None = None


class ArtifactFileEntry(BaseModel):
    file_id: int
    role: Literal["primary", "preview"]
    filename: str
    mime_type: str
    # The DB column is NOT NULL with a positive-size check; code wins over the
    # plan's ``int | None``.
    size_bytes: int
    content_url: str


class FlashcardStudyState(BaseModel):
    generation: int
    marks: dict[str, Literal["good", "again"]]
    order: list[int]


class QuizState(BaseModel):
    generation: int
    mode: Literal["all", "missed"]
    active_question_indices: list[int]
    answers: dict[str, int]
    skipped_question_indices: list[int]


class ArtifactManifest(BaseModel):
    artifact_id: int
    document_id: int
    title: str
    format: str
    generation: int
    markdown_representation: str | None
    files: list[ArtifactFileEntry]
    updated_at: str | None
    legacy: ArtifactLegacyRef | None = None
    flashcard_study_state: FlashcardStudyState | None = None
    quiz_state: QuizState | None = None
