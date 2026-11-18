"""The narrowed config-loader handlers still fall back the same way."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: bytes) -> None:
    from app import config as config_module

    cfg_dir = tmp_path / "app" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "global_llm_config.yaml").write_bytes(body)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)


def test_invalid_yaml_reads_as_empty(monkeypatch, tmp_path, capsys):
    from app.config import _read_global_config_yaml

    _setup(monkeypatch, tmp_path, b"a: [unclosed\n")
    path = tmp_path / "app" / "config" / "global_llm_config.yaml"
    assert _read_global_config_yaml(str(path)) == {}
    assert "Failed to read global_llm_config.yaml" in capsys.readouterr().out


def test_impossible_date_yaml_reads_as_empty(monkeypatch, tmp_path, capsys):
    from app.config import _read_global_config_yaml

    _setup(monkeypatch, tmp_path, b"released: 2026-02-30\n")
    path = tmp_path / "app" / "config" / "global_llm_config.yaml"
    assert _read_global_config_yaml(str(path)) == {}
    assert "Failed to read global_llm_config.yaml" in capsys.readouterr().out


def test_undecodable_yaml_reads_as_empty(monkeypatch, tmp_path, capsys):
    from app.config import _read_global_config_yaml

    _setup(monkeypatch, tmp_path, b"\xff\xfe\x00bad")
    path = tmp_path / "app" / "config" / "global_llm_config.yaml"
    assert _read_global_config_yaml(str(path)) == {}
    assert "Failed to read" in capsys.readouterr().out


def test_router_settings_fall_back_when_section_is_not_a_mapping(
    monkeypatch, tmp_path, capsys
):
    from app.config import load_router_settings

    _setup(monkeypatch, tmp_path, b"router_settings: 5\n")
    settings = load_router_settings()
    assert settings["routing_strategy"]
    assert "Failed to load router settings" in capsys.readouterr().out


def test_image_gen_configs_fall_back_when_section_is_not_a_list(
    monkeypatch, tmp_path, capsys
):
    from app.config import load_global_image_gen_configs

    _setup(monkeypatch, tmp_path, b"global_image_generation_configs: 5\n")
    assert load_global_image_gen_configs() == []
    assert "Failed to load global image generation configs" in capsys.readouterr().out


def test_image_gen_router_settings_fall_back_when_section_is_not_a_mapping(
    monkeypatch, tmp_path, capsys
):
    from app.config import load_image_gen_router_settings

    _setup(monkeypatch, tmp_path, b"image_generation_router_settings: 5\n")
    settings = load_image_gen_router_settings()
    assert settings["routing_strategy"]
    assert "Failed to load image generation router settings" in capsys.readouterr().out


def test_openrouter_settings_fall_back_when_section_is_not_a_mapping(
    monkeypatch, tmp_path, capsys
):
    from app.config import load_openrouter_integration_settings

    _setup(monkeypatch, tmp_path, b"openrouter_integration: 5\n")
    assert load_openrouter_integration_settings() is None
    assert "Failed to load OpenRouter integration settings" in capsys.readouterr().out
