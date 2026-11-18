"""The committed OpenAPI contract must match the app in the working tree.

CI runs ``scripts/export-openapi.py --check``; this test gives the same verdict
inside the normal pytest run, so a local test run catches drift too.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "export-openapi.py").is_file():
            return parent
    raise RuntimeError("could not locate the repository root")


REPO_ROOT = _repo_root()
EXPORTER_PATH = REPO_ROOT / "scripts" / "export-openapi.py"
CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "openapi.json"
SWIFT_COPY_PATH = (
    REPO_ROOT
    / "clients"
    / "swift"
    / "MonoshiriAPI"
    / "Sources"
    / "MonoshiriAPI"
    / "openapi.json"
)
GENERATOR_CONFIG_PATH = (
    REPO_ROOT
    / "clients"
    / "swift"
    / "MonoshiriAPI"
    / "Sources"
    / "MonoshiriAPI"
    / "openapi-generator-config.yaml"
)

DRIFT_MESSAGE = (
    "API changed: run scripts/export-openapi.py and commit docs/contracts/openapi.json"
)


def _load_exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "monoshiri_export_openapi", EXPORTER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _filtered_operation_ids() -> list[str]:
    """The operationIds the Swift generator is told to download.

    FastAPI derives operationIds from handler names, so a rename silently
    removes a Swift method. Read the config (PyYAML if present; the list is a
    simple YAML sequence, so a regex is enough otherwise).
    """
    text = GENERATOR_CONFIG_PATH.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML ships with the backend venv
        block = re.search(
            r"^\s*operations:\s*$\n((?:[ \t]+-[^\n]*\n?)+)", text, re.MULTILINE
        )
        assert block is not None, (
            f"no filter.operations list in {GENERATOR_CONFIG_PATH}"
        )
        return re.findall(r"-\s*(\S+)", block.group(1))
    config = yaml.safe_load(text)
    return list(config["filter"]["operations"] or [])


@pytest.mark.timeout(120)
def test_committed_contract_is_fresh(tmp_path: Path) -> None:
    exporter = _load_exporter()
    generated_path = tmp_path / "openapi.json"
    exporter.write_contract(generated_path, exporter.render_contract())

    generated = generated_path.read_text(encoding="utf-8")
    assert generated == CONTRACT_PATH.read_text(encoding="utf-8"), DRIFT_MESSAGE
    # The Swift client compiles its own copy; a stale copy targets an older API.
    assert generated == SWIFT_COPY_PATH.read_text(encoding="utf-8"), (
        "Swift client contract copy is stale: run scripts/export-openapi.py"
    )


def test_generator_filter_operation_ids_exist() -> None:
    """Every operationId the Swift config filters on is in the contract."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operation_ids = {
        operation["operationId"]
        for path_item in contract["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    missing = [
        operation_id
        for operation_id in _filtered_operation_ids()
        if operation_id not in operation_ids
    ]
    assert not missing, (
        f"openapi-generator-config.yaml lists operationIds missing from "
        f"docs/contracts/openapi.json: {missing}"
    )
