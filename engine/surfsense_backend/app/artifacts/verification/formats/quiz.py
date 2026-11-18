"""Strict structural checks and indexing projection for quiz artifacts."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import StructuralCheckResult
from .study_text import (
    escape_markdown,
    parse_study_artifact,
    require_schema_version_one,
    study_text_to_markdown,
    validate_study_text,
    validate_title_line,
)

QUIZ_DEFAULT_QUESTIONS = 10
QUIZ_MIN_QUESTIONS = 5
QUIZ_MAX_QUESTIONS = 30
QUIZ_MAX_TITLE_CHARS = 200
QUIZ_MAX_QUESTION_CHARS = 4_000
QUIZ_MAX_OPTION_CHARS = 4_000
QUIZ_MAX_EXPLANATION_CHARS = 12_000

QuizOptionIndex = Annotated[int, Field(strict=True, ge=0, le=3)]


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class QuizQuestionV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question_text: str
    options: list[str]
    correct_option_index: QuizOptionIndex
    explanation_text: str

    @field_validator("question_text")
    @classmethod
    def validate_question_text(cls, value: str) -> str:
        return validate_study_text(
            value,
            field="question_text",
            maximum=QUIZ_MAX_QUESTION_CHARS,
        )

    @field_validator("options")
    @classmethod
    def validate_options(cls, value: list[str]) -> list[str]:
        if len(value) != 4:
            raise ValueError("options must contain exactly four entries")
        seen: set[str] = set()
        for index, option in enumerate(value):
            validate_study_text(
                option,
                field=f"options.{index}",
                maximum=QUIZ_MAX_OPTION_CHARS,
            )
            normalized = _normalized_text(option)
            if normalized in seen:
                raise ValueError("options must be distinct")
            seen.add(normalized)
        return value

    @field_validator("explanation_text")
    @classmethod
    def validate_explanation_text(cls, value: str) -> str:
        return validate_study_text(
            value,
            field="explanation_text",
            maximum=QUIZ_MAX_EXPLANATION_CHARS,
        )


class QuizV1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    title: str
    questions: list[QuizQuestionV1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version(cls, value: Any) -> Any:
        return require_schema_version_one(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return validate_title_line(value, QUIZ_MAX_TITLE_CHARS)

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, value: list[QuizQuestionV1]) -> list[QuizQuestionV1]:
        if not QUIZ_MIN_QUESTIONS <= len(value) <= QUIZ_MAX_QUESTIONS:
            raise ValueError(
                "questions must contain between "
                f"{QUIZ_MIN_QUESTIONS} and {QUIZ_MAX_QUESTIONS} entries"
            )
        seen: set[str] = set()
        for question in value:
            normalized = _normalized_text(question.question_text)
            if normalized in seen:
                raise ValueError("questions contain duplicate question text")
            seen.add(normalized)
        return value


def parse_quiz(data: bytes) -> QuizV1:
    """Parse exact UTF-8 JSON into the closed version-one quiz model."""
    return parse_study_artifact(data, QuizV1, label="quiz artifact", root="quiz")


def check_quiz_json(data: bytes) -> StructuralCheckResult:
    try:
        quiz = parse_quiz(data)
    except ValueError as exc:
        return StructuralCheckResult((str(exc),))
    return StructuralCheckResult(
        (),
        notes=(f"Quiz: schema {quiz.schema_version}, {len(quiz.questions)} questions",),
    )


def quiz_to_markdown(data: bytes) -> str:
    """Project verified quiz JSON into deterministic searchable Markdown."""
    quiz = parse_quiz(data)
    sections = [f"# {escape_markdown(quiz.title.strip())}"]
    labels = ("A", "B", "C", "D")
    for index, question in enumerate(quiz.questions, 1):
        options = "\n".join(
            f"{label}. "
            f"{study_text_to_markdown(option.strip(), field='option', maximum=QUIZ_MAX_OPTION_CHARS)}"
            for label, option in zip(labels, question.options, strict=True)
        )
        correct_index = question.correct_option_index
        section = [
            f"## Question {index}",
            study_text_to_markdown(
                question.question_text.strip(),
                field="question_text",
                maximum=QUIZ_MAX_QUESTION_CHARS,
            ),
            "### Options",
            options,
            "### Correct answer",
            (
                f"{labels[correct_index]}. "
                f"{study_text_to_markdown(question.options[correct_index].strip(), field='option', maximum=QUIZ_MAX_OPTION_CHARS)}"
            ),
            "### Explanation",
            study_text_to_markdown(
                question.explanation_text.strip(),
                field="explanation_text",
                maximum=QUIZ_MAX_EXPLANATION_CHARS,
            ),
        ]
        sections.append("\n\n".join(section))
    return "\n\n".join(sections) + "\n"
