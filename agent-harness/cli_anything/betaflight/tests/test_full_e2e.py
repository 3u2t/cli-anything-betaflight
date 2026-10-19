"""Opt-in tests against a real flight controller (read-only).

Run with:
    set BETAFLIGHT_E2E_PORT=COM4
    pytest cli_anything/betaflight/tests/test_full_e2e.py
"""

from __future__ import annotations

import os

import pytest

from cli_anything.betaflight.core import device as device_core

PORT = os.environ.get("BETAFLIGHT_E2E_PORT")

pytestmark = pytest.mark.skipif(
    not PORT, reason="set BETAFLIGHT_E2E_PORT=COMx to test real hardware"
)


@pytest.fixture(scope="module")
def dev():
    device = device_core.Device(port=PORT)
    try:
        yield device
    finally:
        device.close()


def test_ports_listed():
    ports = device_core.list_ports()
    assert any(p.device == PORT for p in ports)


def test_info(dev):
    info = dev.info()
    assert info["variant"] in {"BTFL", "CLFL", "INAV"}
    assert info["board"]
    assert info["firmware"]


def test_status(dev):
    status = dev.status()
    assert "analog" in status
    assert isinstance(status["motors"], list)


def test_config_diff(dev):
    text = dev.config_diff()
    assert isinstance(text, str)
