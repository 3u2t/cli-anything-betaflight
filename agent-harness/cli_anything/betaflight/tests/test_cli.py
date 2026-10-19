"""Click-level tests for the Betaflight harness."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything.betaflight.core import source
from cli_anything.betaflight.betaflight_cli import cli

try:
    REAL_REPO = source.find_repo()
except source.RepoError:  # pragma: no cover - depends on the machine
    REAL_REPO = None

pytestmark = pytest.mark.skipif(REAL_REPO is None, reason="no Betaflight checkout")


def invoke(*args: str):
    runner = CliRunner()
    return runner.invoke(cli, ["--repo", str(REAL_REPO), *args])


def test_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "1.0.0" in result.output


def test_repo_info_json():
    result = invoke("--json", "repo", "info")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["root"]
    assert data["board_count"] > 0
    assert "commit" in data["git"]


def test_repo_doctor_reports_tools():
    result = invoke("repo", "doctor")
    assert result.exit_code == 0, result.output
    assert "make:" in result.output
    assert "can build:" in result.output


def test_boards_list_json():
    result = invoke("--json", "boards", "list", "--search", "acct")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert any(board["name"] == "ACCTON_GFF4" for board in data)


def test_boards_list_human_table():
    result = invoke("boards", "list", "--mcu", "STM32F405")
    assert result.exit_code == 0, result.output
    assert "ACCTON_GFF4" in result.output
    assert "board(s)" in result.output


def test_boards_show_json_contains_defines_and_pins():
    result = invoke("--json", "boards", "show", "ACCTON_GFF4")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["mcu"] == "STM32F405"
    assert data["pins"]["MOTOR1_PIN"]
    assert data["defines"]["BOARD_NAME"] == "ACCTON_GFF4"


def test_boards_show_unknown_board_fails_cleanly():
    result = invoke("boards", "show", "NOPE_NOT_A_BOARD")
    assert result.exit_code != 0
    assert "Unknown board" in result.output


def test_boards_diff_json():
    result = invoke("--json", "boards", "diff", "ACCTON_GFF4", "ACCTON_GFF4")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["summary"]["identical"] is True


def test_boards_validate_errors_only():
    result = invoke("boards", "validate", "ACCTON_GFF4", "--errors-only")
    assert result.exit_code == 0, result.output
    assert "errors" in result.output


def test_boards_mcus_json():
    result = invoke("--json", "boards", "mcus")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["STM32F405"] > 5


def test_build_run_dry_run_does_not_build():
    result = invoke("build", "run", "ACCTON_GFF4", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "make CONFIG=ACCTON_GFF4" in result.output
    assert "(cwd" in result.output


def test_build_clean_requires_yes():
    result = invoke("build", "clean", "ACCTON_GFF4")
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_build_flash_requires_yes():
    result = invoke("build", "flash", "ACCTON_GFF4")
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_device_list_json():
    result = invoke("--json", "device", "list")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)


def test_device_config_set_requires_yes_before_touching_hardware():
    result = invoke("device", "config", "set", "gyro_lpf1_static_hz", "250")
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_device_save_requires_yes():
    result = invoke("device", "save")
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_device_reboot_requires_yes():
    result = invoke("device", "reboot")
    assert result.exit_code != 0
    assert "--yes" in result.output


def test_device_cli_blocks_destructive_commands():
    result = invoke("device", "cli", "defaults")
    assert result.exit_code != 0
    assert "--force" in result.output


def test_device_cli_blocks_set_without_force():
    result = invoke("device", "cli", "set", "gyro_lpf1_static_hz", "250")
    assert result.exit_code != 0
    assert "--force" in result.output
