"""Prompt loaders for the knowledge_base subagent."""

from __future__ import annotations

from app.agents.chat.multi_agent_chat.subagents.shared.md_file_reader import (
    read_md_file,
)


def load_system_prompt() -> str:
    return read_md_file(__package__, "system_prompt_cloud").strip()


def load_readonly_system_prompt() -> str:
    return read_md_file(__package__, "system_prompt_readonly_cloud").strip()


def load_description() -> str:
    return read_md_file(__package__, "description").strip() or (
        "Handles knowledge-base reads, writes, edits, and organisation."
    )


def load_readonly_description() -> str:
    return read_md_file(__package__, "description_readonly").strip()
