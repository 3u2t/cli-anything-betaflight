"""Unit tests for the CLI core: MSP framing/paging and config.h parsing.

No backend software, compiler or flight controller is required.
"""

from __future__ import annotations

import struct
import textwrap
from pathlib import Path

import pytest

from cli_anything.betaflight.core import build, source
from cli_anything.betaflight.utils import msp

def _v1_response(command: int, payload: bytes = b"") -> bytes:
    body = bytes([len(payload), command]) + payload
    checksum = 0
    for byte in body:
        checksum ^= byte
    return b"$M>" + body + bytes([checksum])


def _v2_response(command: int, payload: bytes = b"", flags: int = 0) -> bytes:
    body = struct.pack("<BHH", flags, command, len(payload)) + payload
    return b"$X>" + body + bytes([msp.crc8_dvb_s2(body)])


class FakeTransport:
    """Feeds scripted bytes back to the client; records what was written."""

    def __init__(self, handler):
        self.handler = handler
        self.written: list[bytes] = []
        self.pending = bytearray()
        self.port = "FAKE"

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        self.pending.extend(self.handler(bytes(data)))
        return len(data)

    def read(self, size: int = 1) -> bytes:
        if not self.pending:
            return b""
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def reset_input(self) -> None:
        # Called before each request; a fake that answered immediately would
        # otherwise lose its response, so keep the queue.
        pass

    def close(self) -> None:
        pass


def parse_request(data: bytes):
    if data.startswith(b"$M<"):
        size = data[3]
        command = data[4]
        payload = data[5 : 5 + size]
        return 1, command, payload
    if data.startswith(b"$X<"):
        flags, command, size = struct.unpack_from("<BHH", data, 3)
        payload = data[8 : 8 + size]
        return 2, command, payload
    raise AssertionError(f"not an MSP request: {data!r}")


def test_crc8_check_value():
    assert msp.crc8_dvb_s2(b"123456789") == 0xBC


def test_encode_v1_layout():
    frame = msp.encode_v1(1, b"")
    assert frame == b"$M<\x00\x01\x01"


def test_encode_v2_layout():
    frame = msp.encode_v2(0x3012, b"version")
    assert frame[:3] == b"$X<"
    flags, command, size = struct.unpack_from("<BHH", frame, 3)
    assert (flags, command, size) == (0, 0x3012, 7)
    assert frame[8:15] == b"version"
    assert frame[-1] == msp.crc8_dvb_s2(frame[3:-1])


def test_request_v1_roundtrip():
    transport = FakeTransport(lambda data: _v1_response(3, b"\x04\x05\x00"))
    client = msp.MspClient(transport, settle=0)
    frame = client.request_v1(3)
    assert (frame.version, frame.command, frame.payload) == (1, 3, b"\x04\x05\x00")


def test_request_v2_roundtrip_selects_v2_for_high_commands():
    transport = FakeTransport(lambda data: _v2_response(0x3010, b"name = 1"))
    client = msp.MspClient(transport, settle=0)
    frame = client.request(0x3010)
    assert frame.version == 2
    assert frame.payload == b"name = 1"
    assert parse_request(transport.written[0])[0] == 2


def test_checksum_error_is_detected():
    def handler(_data):
        frame = bytearray(_v1_response(3, b"\x01"))
        frame[-1] ^= 0xFF
        return bytes(frame)

    client = msp.MspClient(FakeTransport(handler), settle=0)
    with pytest.raises(msp.MspChecksumError):
        client.request_v1(3)


def test_error_frame_raises_command_error():
    client = msp.MspClient(FakeTransport(lambda _d: b"$M!"), settle=0)
    with pytest.raises(msp.MspCommandError):
        client.request_v1(1)


def test_timeout_raises():
    client = msp.MspClient(FakeTransport(lambda _d: b""), settle=0)
    with pytest.raises(msp.MspTimeout):
        client.request_v1(1, timeout=0.2)


def test_split_setting():
    assert msp.split_setting("gyro_lpf1_static_hz = 250") == (
        "gyro_lpf1_static_hz",
        "250",
    )
    assert msp.split_setting("no equals") is None


def test_msp2_cli_paging_concatenates_windows():
    full = b"A" * 10
    requests: list[bytes] = []

    def handler(data):
        _, command, payload = parse_request(data)
        assert command == msp.MSP2_CLI_COMMAND
        requests.append(payload)
        if b"\x00" in payload:
            name, offset = payload.split(b"\x00", 1)
            start = struct.unpack("<H", offset)[0]
        else:
            start = 0
        window = full[start : start + 4]
        return _v2_response(command, struct.pack("<HB", len(full), 0) + window)

    client = msp.MspClient(FakeTransport(handler), settle=0)
    text = client.cli_command("version")
    assert text == "A" * 10
    assert requests[0] == b"version"
    assert requests[1] == b"version\x00\x04\x00"
    assert requests[2] == b"version\x00\x08\x00"


def test_msp2_cli_refused():
    def handler(data):
        _, command, _ = parse_request(data)
        return _v2_response(
            command,
            struct.pack("<HB", 0, msp.MSP2_CLI_COMMAND_FLAG_REFUSED),
        )

    client = msp.MspClient(FakeTransport(handler), settle=0)
    with pytest.raises(msp.MspRefused):
        client.cli_command("bad-command")


def test_legacy_cli_fallback():
    """MSP2 CLI unknown -> MSP_CLI 217 -> raw text prompt."""
    state = {"entered": False}

    def handler(data: bytes) -> bytes:
        if data.startswith(b"$X<"):
            return b"$M!"  # firmware without MSP2 CLI support
        if not data.startswith(b"$"):
            # raw text after the legacy CLI prompt
            text = data.decode()
            if text.startswith("version"):
                return b"version\r\nBetaflight 2026.12.0\r\n# "
            if text.startswith("exit"):
                return b"# "
            return b""
        _, command, payload = parse_request(data)
        if command == msp.MSP_CLI:
            state["entered"] = True
            return _v1_response(command) + b"\r\nEntering CLI Mode\r\n# "
        return b""

    client = msp.MspClient(FakeTransport(handler), settle=0)
    output = client.cli_command("version")
    assert output == "Betaflight 2026.12.0"


def test_box_names_parsing():
    def handler(_data):
        return _v1_response(msp.MSP_BOXNAMES, b"ARM;ANGLE;HORIZON\x00")

    client = msp.MspClient(FakeTransport(handler), settle=0)
    assert client.box_names() == ["ARM", "ANGLE", "HORIZON"]


def test_status_layout_uses_u16_i2c_when_plausible():
    payload = struct.pack("<HHHIBH", 125, 0, 0b100001, 0x2, 2, 0)
    client = msp.MspClient(
        FakeTransport(lambda _d: _v1_response(msp.MSP_STATUS, payload)), settle=0
    )
    status = client.status()
    assert status["layout"] == "u16-i2c"
    assert status["profile"] == 2
    assert status["flight_mode_flags"] == 0x2


HEADER = """\
/* block comment
   #define NOT_REAL 1
*/
#pragma once
#define FC_TARGET_MCU     STM32F405
#define BOARD_NAME        TESTBOARD
#define MANUFACTURER_ID   TEST
#define USE_GYRO
#define USE_ACC
#define MOTOR1_PIN        PC6 // TIM8_CH1
#define BEEPER_PIN        PB15
#define TIMER_PIN_MAPPING TIM_USE_MOTOR 1 0
#define FUNCTION_LIKE(x)  ((x) + 1)
"""

OTHER = """\
#define FC_TARGET_MCU     STM32H743
#define BOARD_NAME        OTHERBOARD
#define MANUFACTURER_ID   TEST
#define USE_GYRO
#define USE_BARO
#define MOTOR1_PIN        PA0
#define TIMER_PIN_MAPPING TIM_USE_MOTOR 1 0
"""


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    (tmp_path / "src" / "main" / "cli").mkdir(parents=True)
    configs = tmp_path / "src" / "config" / "configs"
    board = configs / "TEST" / "TESTBOARD"
    board.mkdir(parents=True)
    (board / "config.h").write_text(HEADER, encoding="utf-8")
    (board / "config.mk").write_text("", encoding="utf-8")
    other = configs / "TEST" / "OTHERBOARD"
    other.mkdir()
    (other / "config.h").write_text(OTHER, encoding="utf-8")
    (configs / "FLATBOARD").mkdir()
    (configs / "FLATBOARD" / "config.h").write_text(
        "#define FC_TARGET_MCU STM32F411\n#define BOARD_NAME FLATBOARD\n",
        encoding="utf-8",
    )
    return tmp_path


def test_parse_defines_strips_comments_and_function_macros():
    defines = source.parse_defines(HEADER)
    assert defines["FC_TARGET_MCU"] == "STM32F405"
    assert defines["MOTOR1_PIN"] == "PC6"
    assert "NOT_REAL" not in defines
    assert "FUNCTION_LIKE" not in defines
    assert "pragma" not in defines


def test_find_repo(tmp_path):
    repo = make_repo(tmp_path)
    assert source.find_repo(repo) == repo.resolve()


def test_index_loads_grouped_and_flat_boards(tmp_path):
    index = source.BoardIndex(make_repo(tmp_path))
    boards = {b.name: b for b in index.all()}
    assert set(boards) == {"TESTBOARD", "OTHERBOARD", "FLATBOARD"}
    assert boards["TESTBOARD"].manufacturer == "TEST"
    assert boards["TESTBOARD"].mcu == "STM32F405"
    assert boards["FLATBOARD"].manufacturer is None
    assert boards["TESTBOARD"].has_config_mk
    assert not boards["TESTBOARD"].has_config_c


def test_board_pins_and_features(tmp_path):
    board = source.BoardIndex(make_repo(tmp_path)).get("testboard")
    assert board.pins == {"BEEPER_PIN": "PB15", "MOTOR1_PIN": "PC6"}
    assert "GYRO" in board.features


def test_validate_happy_path_has_no_errors(tmp_path):
    index = source.BoardIndex(make_repo(tmp_path))
    issues = index.validate(index.get("TESTBOARD"))
    assert [i for i in issues if i.severity == "error"] == []


def test_validate_name_mismatch(tmp_path):
    repo = make_repo(tmp_path)
    header = repo / "src" / "config" / "configs" / "TEST" / "TESTBOARD" / "config.h"
    header.write_text(
        HEADER.replace("BOARD_NAME        TESTBOARD", "BOARD_NAME        WRONG"),
        encoding="utf-8",
    )
    index = source.BoardIndex(repo)
    codes = {i.code for i in index.validate(index.get("TESTBOARD"))}
    assert "board-name-mismatch" in codes


def test_validate_manufacturer_mismatch(tmp_path):
    repo = make_repo(tmp_path)
    header = repo / "src" / "config" / "configs" / "TEST" / "TESTBOARD" / "config.h"
    header.write_text(
        HEADER.replace("MANUFACTURER_ID   TEST", "MANUFACTURER_ID   NOPE"),
        encoding="utf-8",
    )
    index = source.BoardIndex(repo)
    codes = {i.code for i in index.validate(index.get("TESTBOARD"))}
    assert "manufacturer-mismatch" in codes


def test_validate_missing_mcu_and_bad_format(tmp_path):
    repo = make_repo(tmp_path)
    header = repo / "src" / "config" / "configs" / "TEST" / "TESTBOARD" / "config.h"
    header.write_text(
        HEADER.replace("STM32F405", "NOT_A_CHIP"), encoding="utf-8"
    )
    index = source.BoardIndex(repo)
    codes = {i.code for i in index.validate(index.get("TESTBOARD"))}
    assert "mcu-format" in codes


def test_validate_duplicate_names(tmp_path):
    repo = make_repo(tmp_path)
    twin = repo / "src" / "config" / "configs" / "TEST2" / "TESTBOARD"
    twin.mkdir(parents=True)
    (twin / "config.h").write_text(
        OTHER.replace("OTHERBOARD", "TESTBOARD"), encoding="utf-8"
    )
    index = source.BoardIndex(repo)
    codes = {i.code for i in index.validate()}
    assert "duplicate-name" in codes
    with pytest.raises(source.RepoError):
        index.get("TESTBOARD")


def test_flat_layout_warns(tmp_path):
    index = source.BoardIndex(make_repo(tmp_path))
    issues = index.validate(index.get("FLATBOARD"))
    assert any(i.code == "flat-layout" for i in issues)


def test_diff_boards_reports_categories(tmp_path):
    index = source.BoardIndex(make_repo(tmp_path))
    diff = source.diff_boards(index.get("TESTBOARD"), index.get("OTHERBOARD"))
    changed = {entry["define"]: entry for entry in diff["changed"]}
    assert changed["FC_TARGET_MCU"]["category"] == "mcu"
    assert changed["MOTOR1_PIN"]["category"] == "pin"
    added = {entry["define"] for entry in diff["added"]}
    assert "USE_BARO" in added
    removed = {entry["define"] for entry in diff["removed"]}
    assert "USE_ACC" in removed


def test_real_checkout_smoke():
    try:
        repo = source.find_repo()
    except source.RepoError:
        pytest.skip("no Betaflight checkout available")
    index = source.BoardIndex(repo)
    if not index.available:
        pytest.skip("src/config submodule not initialized")
    boards = index.all()
    assert len(boards) > 500
    assert any(b.name == "ACCTON_GFF4" for b in boards)
    assert build.firmware_version(repo) != "unknown"


def test_real_board_has_pins_and_features():
    try:
        repo = source.find_repo()
        index = source.BoardIndex(repo)
        board = index.get("ACCTON_GFF4")
    except (source.RepoError,):
        pytest.skip("no Betaflight checkout available")
    assert board.mcu == "STM32F405"
    assert board.pins
    assert "GYRO" in board.features

