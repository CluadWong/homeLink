"""启动期配置缺失/损坏：命令行与 `create_app_from_env` 给同一段中文提示。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from homelink.core.config import ConfigError, ConfigService
from homelink.interface.api.app import create_app_from_env

_REPO_ROOT = Path(__file__).resolve().parents[1]

_VALID_SETTINGS = """gateway:
  mode: mock
  ir:
    base_url: ""
    username: ""
    password: ""
"""

_VALID_DEVICES = """rooms: []
"""


def _write(config_dir: Path, name: str, text: str) -> None:
    (config_dir / name).write_text(text, encoding="utf-8")


def test_missing_settings_error_names_file_and_shows_example(tmp_path):
    with pytest.raises(ConfigError) as caught:
        ConfigService.load(tmp_path)

    message = str(caught.value)
    assert str(tmp_path / "settings.yaml") in message
    assert "最小可用示例" in message
    assert "gateway:" in message
    assert "docs/README.md" in message


def test_missing_devices_error_names_file_and_shows_example(tmp_path):
    _write(tmp_path, "settings.yaml", _VALID_SETTINGS)

    with pytest.raises(ConfigError) as caught:
        ConfigService.load(tmp_path)

    message = str(caught.value)
    assert str(tmp_path / "devices.yaml") in message
    assert "rooms: []" in message


def test_broken_settings_yaml_error_is_friendly(tmp_path):
    _write(tmp_path, "settings.yaml", "gateway: [\n")

    with pytest.raises(ConfigError) as caught:
        ConfigService.load(tmp_path)

    message = str(caught.value)
    assert str(tmp_path / "settings.yaml") in message
    assert "无法解析" in message
    assert "docs/README.md" in message


def test_settings_top_level_not_mapping_is_friendly(tmp_path):
    _write(tmp_path, "settings.yaml", "- mock\n")

    with pytest.raises(ConfigError) as caught:
        ConfigService.load(tmp_path)

    assert "顶层必须是映射" in str(caught.value)


def test_minimal_example_files_are_enough_to_build_app(tmp_path):
    _write(tmp_path, "settings.yaml", _VALID_SETTINGS)
    _write(tmp_path, "devices.yaml", _VALID_DEVICES)

    app = create_app_from_env(tmp_path, tmp_path / "data" / "homelink.db")

    assert app.state.context.config.settings.gateway.mode == "mock"


def test_cli_entrypoint_prints_same_message_and_exits_nonzero(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    env = {
        **os.environ,
        "PYTHONPATH": str(_REPO_ROOT / "src"),
        "HOMELINK_CONFIG_DIR": str(config_dir),
        "HOMELINK_DB_PATH": str(tmp_path / "cli.sqlite"),
    }
    result = subprocess.run(
        [sys.executable, "-c", "import homelink.interface.api.app"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "配置不可用" in result.stderr
    assert str(config_dir / "settings.yaml") in result.stderr
    assert "Traceback" not in result.stderr

    with pytest.raises(ConfigError) as caught:
        create_app_from_env(config_dir, tmp_path / "direct.sqlite")
    assert str(caught.value) in result.stderr
