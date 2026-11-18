"""PostHog telemetry is gone: no app module imports it and the analytics shim is inert."""

import ast
import socket
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"


def test_posthog_package_not_importable_from_app():
    offenders = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(n == "posthog" or n.startswith("posthog.") for n in names):
                offenders.append(str(path.relative_to(APP)))
    assert offenders == []


def test_analytics_capture_is_noop(monkeypatch):
    from app.observability.analytics import posthog as analytics

    def boom(*_a, **_k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", boom)
    assert analytics.capture("evt", distinct_id="u", properties={"a": 1}) is None
    assert analytics.group_identify("workspace", "1", {"a": 1}) is None
    assert analytics.is_enabled() is False
    assert analytics.get_client() is None
    assert analytics.shutdown() is None
