"""Strict structural checks and indexing projection for flashcard decks."""

from __future__ import annotations

import unicodedata
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)

from .base import StructuralCheckResult
from .study_text import (
    escape_markdown as _escape_markdown,
    parse_study_artifact,
    require_schema_version_one,
    study_text_to_markdown as _text_to_markdown,
    validate_study_text as _validate_text,
    validate_title_line,
)

# Published schema versions are immutable read contracts. Add a V2 model/parser
# for incompatible changes instead of tightening V1 in place.
FLASHCARDS_MIN_CARDS = 15
FLASHCARDS_MAX_CARDS = 100
FLASHCARDS_MAX_TITLE_CHARS = 200
FLASHCARDS_MAX_FRONT_CHARS = 4_000
FLASHCARDS_MAX_BACK_CHARS = 12_000
FLASHCARDS_MAX_HINT_CHARS = 2_000


class FlashcardV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    front_text: str
    back_text: str
    hint_text: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_hint(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "hint_text" in value
            and value["hint_text"] is None
        ):
            raise ValueError("hint_text must be omitted instead of null")
        return value

    @field_validator("front_text")
    @classmethod
    def validate_front(cls, value: str) -> str:
        return _validate_text(
            value,
            field="front_text",
            maximum=FLASHCARDS_MAX_FRONT_CHARS,
        )

    @field_validator("back_text")
    @classmethod
    def validate_back(cls, value: str) -> str:
        return _validate_text(
            value,
            field="back_text",
            maximum=FLASHCARDS_MAX_BACK_CHARS,
        )

    @field_validator("hint_text")
    @classmethod
    def validate_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_text(
            value,
            field="hint_text",
            maximum=FLASHCARDS_MAX_HINT_CHARS,
        )


class FlashcardDeckV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    title: str
    cards: list[FlashcardV1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: Any) -> Any:
        return require_schema_version_one(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return validate_title_line(value, FLASHCARDS_MAX_TITLE_CHARS)

    @field_validator("cards")
    @classmethod
    def validate_cards(cls, value: list[FlashcardV1]) -> list[FlashcardV1]:
        if not FLASHCARDS_MIN_CARDS <= len(value) <= FLASHCARDS_MAX_CARDS:
            raise ValueError(
                "cards must contain between "
                f"{FLASHCARDS_MIN_CARDS} and {FLASHCARDS_MAX_CARDS} entries"
            )

        seen: set[str] = set()
        for card in value:
            normalized = " ".join(
                unicodedata.normalize("NFKC", card.front_text).casefold().split()
            )
            if normalized in seen:
                raise ValueError("cards contain duplicate fronts")
            seen.add(normalized)
        return value


def parse_flashcards_deck(data: bytes) -> FlashcardDeckV1:
    """Parse exact UTF-8 JSON into the closed version-one deck model."""
    return parse_study_artifact(
        data, FlashcardDeckV1, label="flashcard artifact", root="deck"
    )


def check_flashcards_json(data: bytes) -> StructuralCheckResult:
    try:
        deck = parse_flashcards_deck(data)
    except ValueError as exc:
        return StructuralCheckResult((str(exc),))
    return StructuralCheckResult(
        (),
        notes=(
            f"Flashcard deck: schema {deck.schema_version}, {len(deck.cards)} cards",
        ),
    )


def flashcards_to_markdown(data: bytes) -> str:
    """Project a verified JSON deck into searchable, readable Markdown."""
    deck = parse_flashcards_deck(data)
    sections = [f"# {_escape_markdown(deck.title.strip())}"]
    for index, card in enumerate(deck.cards, 1):
        section = [
            f"## Card {index}",
            "### Front",
            _text_to_markdown(
                card.front_text.strip(),
                field="front_text",
                maximum=FLASHCARDS_MAX_FRONT_CHARS,
            ),
            "### Back",
            _text_to_markdown(
                card.back_text.strip(),
                field="back_text",
                maximum=FLASHCARDS_MAX_BACK_CHARS,
            ),
        ]
        if card.hint_text is not None:
            section.extend(
                (
                    "### Hint",
                    _text_to_markdown(
                        card.hint_text.strip(),
                        field="hint_text",
                        maximum=FLASHCARDS_MAX_HINT_CHARS,
                    ),
                )
            )
        sections.append("\n\n".join(section))
    return "\n\n".join(sections) + "\n"
