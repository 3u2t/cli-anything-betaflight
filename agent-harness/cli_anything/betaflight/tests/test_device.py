"""Tests for the GUI-style configuration layer (no hardware needed)."""

from __future__ import annotations

import struct

import pytest
from click.testing import CliRunner

from cli_anything.betaflight.betaflight_cli import cli
from cli_anything.betaflight.core import device as device_core
from cli_anything.betaflight.core import source

try:
    REAL_REPO = source.find_repo()
except source.RepoError:  # pragma: no cover
    REAL_REPO = None


def test_parse_settings_ignores_comments_and_ranges():
    text = (
        "# version\n"
        "# Betaflight / STM32F405 (S405) 4.5.0\n"
        "gyro_lpf1_static_hz = 250\n"
        "craft_name = My Quad\n"
        "profile 0\n"
        "\n"
    )
    items = device_core.parse_settings(text)
    assert items == [
        {"name": "gyro_lpf1_static_hz", "value": "250"},
        {"name": "craft_name", "value": "My Quad"},
    ]


def test_parse_features_sections():
    text = (
        "Enabled: GPS TELEMETRY\n"
        "Available: LED_STRIP AIRMODE\n"
        "Unavailable: SERVOS\n"
    )
    data = device_core.parse_features(text)
    assert data["enabled"] == ["GPS", "TELEMETRY"]
    assert data["available"] == ["LED_STRIP", "AIRMODE"]
    assert data["unavailable"] == ["SERVOS"]


def test_parse_aux_lines():
    text = (
        "aux 0 0 0 900 900 0 0\n"
        "aux 1 1 2 1300 1700 0 0\n"
        "not an aux line\n"
    )
    modes = device_core.parse_aux(text)
    assert [m["mode_id"] for m in modes] == [0, 1]
    assert modes[1] == {
        "index": 1,
        "mode_id": 1,
        "aux_channel": 2,
        "start": 1300,
        "end": 1700,
        "logic": 0,
    }


def test_restore_commands_strips_comments_save_and_defaults():
    dump = (
        "# Betaflight backup\n"
        "\n"
        "set gyro_lpf1_static_hz = 250\n"
        "feature GPS\n"
        "defaults\n"
        "save\n"
    )
    assert device_core.restore_commands(dump) == [
        "set gyro_lpf1_static_hz = 250",
        "feature GPS",
    ]


def test_motor_payload_only_spins_selected_motor():
    payload = device_core.build_motor_payload(2, 1100)
    values = struct.unpack("<8H", payload)
    assert values == (1000, 1100, 1000, 1000, 1000, 1000, 1000, 1000)
    assert struct.unpack("<8H", device_core.stop_motor_payload()) == (1000,) * 8


def test_motor_payload_validation():
    with pytest.raises(device_core.DeviceError):
        device_core.build_motor_payload(0, 1100)
    with pytest.raises(device_core.DeviceError):
        device_core.build_motor_payload(1, 2100)


def test_destructive_detection_allows_read_commands():
    assert device_core.is_destructive("status") is False
    assert device_core.is_destructive("feature") is False
    assert device_core.is_destructive("aux") is False
    assert device_core.is_destructive("serial") is False
    assert device_core.is_destructive("get gyro") is False
    assert device_core.is_destructive("feature GPS") is True
    assert device_core.is_destructive("aux 0 0 0 900 900 0") is True
    assert device_core.is_destructive("set gyro_lpf1_static_hz = 250") is True
    assert device_core.is_destructive("defaults") is True


def test_cli_guards_before_opening_a_port(monkeypatch, tmp_path):
    """Write commands must fail on the guard, not on a missing controller."""

    def boom(*_args, **_kwargs):
        raise AssertionError("the CLI opened a serial port before the guard")

    dump = tmp_path / "backup.txt"
    dump.write_text("set gyro_lpf1_static_hz = 250\n", encoding="utf-8")
    firmware = tmp_path / "firmware.hex"
    firmware.write_text(":00000001FF\n", encoding="utf-8")

    monkeypatch.setattr(device_core, "Device", boom)
    runner = CliRunner()
    cases = [
        ["device", "features", "--enable", "GPS"],
        ["device", "restore", str(dump)],
        ["device", "motor", "--motor", "1"],
        ["device", "acc-calibrate"],
        ["device", "flash", str(firmware)],
    ]
    for args in cases:
        result = runner.invoke(cli, ["--repo", str(REAL_REPO or "."), *args])
        assert result.exit_code != 0, args
        assert "--yes" in result.output, args


def test_features_rejects_two_modes():
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--repo", str(REAL_REPO or "."), "device", "features",
         "--enable", "GPS", "--disable", "GPS", "--yes"],
    )
    assert result.exit_code != 0
    assert "either" in result.output.lower()
