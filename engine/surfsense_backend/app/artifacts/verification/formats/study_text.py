"""Shared plain-text and bounded LaTeX contract for study artifacts."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

MARKDOWN_SPECIAL_CHARS = frozenset("\\`*_{}[]<>()#+-.!|>")


def has_control_character(value: str) -> bool:
    return (
        any(ord(character) < 32 and character not in "\t\n\r" for character in value)
        or "\x7f" in value
    )


def _is_escaped(value: str, index: int) -> bool:
    preceding_backslashes = 0
    index -= 1
    while index >= 0 and value[index] == "\\":
        preceding_backslashes += 1
        index -= 1
    return preceding_backslashes % 2 == 1


def _validate_latex(latex: str, *, field: str) -> None:
    if not latex.strip():
        raise ValueError(f"{field} contains an empty LaTeX expression")

    brace_depth = 0
    for index, character in enumerate(latex):
        if _is_escaped(latex, index):
            continue
        if character == "{":
            brace_depth += 1
        elif character == "}":
            brace_depth -= 1
            if brace_depth < 0:
                break
    if brace_depth != 0:
        raise ValueError(f"{field} contains unbalanced LaTeX braces")


def split_text_and_latex(
    value: str, *, field: str, maximum: int
) -> list[tuple[Literal["text", "inline", "display"], str]]:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    if has_control_character(value):
        raise ValueError(f"{field} contains unsupported control characters")

    segments: list[tuple[Literal["text", "inline", "display"], str]] = []
    text_start = 0
    index = 0
    while index < len(value):
        delimiter = value[index : index + 2]
        if delimiter in {r"\)", r"\]"} and not _is_escaped(value, index):
            raise ValueError(f"{field} contains an unmatched LaTeX delimiter")
        if delimiter not in {r"\(", r"\["} or _is_escaped(value, index):
            index += 1
            continue

        if index > text_start:
            segments.append(("text", value[text_start:index]))
        kind: Literal["inline", "display"] = (
            "inline" if delimiter == r"\(" else "display"
        )
        closing = r"\)" if kind == "inline" else r"\]"
        latex_start = index + 2
        index = latex_start
        while index < len(value):
            candidate = value[index : index + 2]
            if candidate in {r"\(", r"\["} and not _is_escaped(value, index):
                raise ValueError(f"{field} contains nested LaTeX delimiters")
            if candidate in {r"\)", r"\]"} and not _is_escaped(value, index):
                if candidate != closing:
                    raise ValueError(f"{field} contains mismatched LaTeX delimiters")
                latex = value[latex_start:index]
                _validate_latex(latex, field=field)
                segments.append((kind, latex))
                index += 2
                text_start = index
                break
            index += 1
        else:
            raise ValueError(f"{field} contains an unclosed LaTeX delimiter")

    if text_start < len(value):
        segments.append(("text", value[text_start:]))
    return segments


def validate_study_text(value: str, *, field: str, maximum: int) -> str:
    split_text_and_latex(value, field=field, maximum=maximum)
    return value


def escape_markdown(value: str) -> str:
    return "".join(
        f"\\{character}" if character in MARKDOWN_SPECIAL_CHARS else character
        for character in value
    )


def study_text_to_markdown(value: str, *, field: str, maximum: int) -> str:
    return "".join(
        escape_markdown(content)
        if kind == "text"
        else f"\\({content}\\)"
        if kind == "inline"
        else f"\\[\n{content}\n\\]"
        for kind, content in split_text_and_latex(value, field=field, maximum=maximum)
    )


def require_schema_version_one(value: Any) -> Any:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be the integer 1")
    return value


def validate_title_line(value: str, maximum: int) -> str:
    if not value.strip():
        raise ValueError("title must not be empty")
    if len(value) > maximum:
        raise ValueError(f"title exceeds {maximum} characters")
    if "\n" in value or "\r" in value or has_control_character(value):
        raise ValueError("title must be one line without control characters")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON number: {value}")


def parse_study_artifact[ModelT: BaseModel](
    data: bytes, model: type[ModelT], *, label: str, root: str
) -> ModelT:
    """Parse exact UTF-8 JSON into the closed ``model``; ``ValueError`` on any defect."""
    if not data:
        raise ValueError(f"{label} is empty")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{label} must not contain a UTF-8 byte-order mark")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{label} must be valid UTF-8") from None
    if not text.strip():
        raise ValueError(f"{label} is empty")
    if has_control_character(text):
        raise ValueError(f"{label} contains unsupported control characters")

    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is not valid strict JSON: {exc}") from None

    try:
        return model.model_validate(value)
    except ValidationError as exc:
        findings = []
        for error in exc.errors(include_url=False):
            location = ".".join(str(part) for part in error["loc"]) or root
            findings.append(f"{location}: {error['msg']}")
        raise ValueError("; ".join(findings)) from None
