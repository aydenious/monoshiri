"""System-prompt assembly tests for the LIVE filesystem middleware.

Ported from ``TestModeSpecificPrompts`` in the former
``tests/unit/middleware/test_filesystem_middleware.py`` (which exercised the
dead twin ``app.agents.chat.shared.middleware.filesystem._build_filesystem_system_prompt``).

These drive the production ``build_system_prompt`` so the prompt the model
actually receives keeps the cloud persistence rules, and the sandbox section
appears only when available.
"""

from __future__ import annotations

import pytest

from app.agents.chat.multi_agent_chat.shared.middleware.filesystem.system_prompt import (
    build_system_prompt,
)

pytestmark = pytest.mark.unit


class TestSystemPrompt:
    def test_prompt_has_cloud_persistence_rules(self):
        prompt = build_system_prompt(sandbox_available=False)
        assert "Local Folder Mode" not in prompt
        assert "mount-prefixed" not in prompt
        assert "Persistence Rules" in prompt
        assert "/documents" in prompt
        assert "temp_" in prompt

    def test_sandbox_addendum_appended_when_available(self):
        prompt = build_system_prompt(sandbox_available=True)
        assert "execute_code" in prompt
        assert "Code Execution" in prompt

    def test_sandbox_addendum_absent_when_unavailable(self):
        prompt = build_system_prompt(sandbox_available=False)
        assert "execute_code" not in prompt
