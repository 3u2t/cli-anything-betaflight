"""MSP (MultiWii Serial Protocol) v1/v2 client for Betaflight flight controllers.

The framing and command ids in this module mirror the Betaflight firmware source
(``src/main/msp/msp_protocol.h`` and ``msp_protocol_v2_betaflight.h``).

Two ways of running the firmware's own CLI are supported:

* ``MSP2_CLI_COMMAND`` (``0x3012``) - the modern paged request/response API used
  by current Betaflight releases.
* ``MSP_CLI`` (``217``) - the legacy mode that switches the serial link to raw
  text until ``exit`` is sent. Used as a fallback for older firmware.

Only read-only traffic happens unless the caller explicitly asks for a write.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

# --- MSP v1 command ids -----------------------------------------------------

MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_BUILD_INFO = 5
MSP_NAME = 10
MSP_STATUS = 101
MSP_RAW_IMU = 102
MSP_MOTOR = 104
MSP_RC = 105
MSP_ATTITUDE = 108
MSP_ANALOG = 110
MSP_BOXNAMES = 116
MSP_BOXIDS = 119
MSP_BATTERY_STATE = 130
MSP_UID = 160
MSP_ACC_CALIBRATION = 205
MSP_SET_MOTOR = 214
MSP_CLI = 217
MSP_EEPROM_WRITE = 250

# --- MSP v2 command ids -----------------------------------------------------

MSP2_CLI_SETTING = 0x3010
MSP2_CLI_SETTING_INFO = 0x3011
MSP2_CLI_COMMAND = 0x3012

MSP2_CLI_COMMAND_FLAG_TRUNCATED = 1 << 0
MSP2_CLI_COMMAND_FLAG_REFUSED = 1 << 1

MSP_V1_MAX_PAYLOAD = 255
MSP_V1_MAX_COMMAND = 255
MSP_V2_MAX_PAYLOAD = 0xFFFF


class MspError(RuntimeError):
    """Base class for MSP failures."""


class MspTimeout(MspError):
    """No complete frame arrived before the deadline."""


class MspCommandError(MspError):
    """The flight controller answered with an error frame (``$M!``)."""


class MspChecksumError(MspError):
    """A frame failed its checksum."""


class MspRefused(MspError):
    """The firmware refused a CLI command."""


def crc8_dvb_s2(data: bytes) -> int:
    """CRC-8/DVB-S2 as used by MSP v2."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def encode_v1(command: int, payload: bytes = b"") -> bytes:
    """Build an MSP v1 request frame."""
    if not 0 <= command <= MSP_V1_MAX_COMMAND:
        raise ValueError(f"MSP v1 command out of range: {command}")
    if len(payload) > MSP_V1_MAX_PAYLOAD:
        raise ValueError("MSP v1 payload too large")
    body = bytes([len(payload), command]) + payload
    checksum = 0
    for byte in body:
        checksum ^= byte
    return b"$M<" + body + bytes([checksum])


def encode_v2(command: int, payload: bytes = b"", flags: int = 0) -> bytes:
    """Build an MSP v2 request frame."""
    if len(payload) > MSP_V2_MAX_PAYLOAD:
        raise ValueError("MSP v2 payload too large")
    body = struct.pack("<BHH", flags, command, len(payload)) + payload
    return b"$X<" + body + bytes([crc8_dvb_s2(body)])


@dataclass
class MspFrame:
    version: int
    command: int
    payload: bytes
    flags: int = 0


class SerialTransport:
    """Thin pyserial wrapper; swapped for a fake in the tests."""

    def __init__(self, port: str, baudrate: int = 115200, read_timeout: float = 0.05):
        import serial  # imported lazily so parsing commands work without hardware

        self.port = port
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=read_timeout,
            write_timeout=2.0,
        )
        self.ser.reset_input_buffer()

    def read(self, size: int = 1) -> bytes:
        return self.ser.read(size)

    def write(self, data: bytes) -> int:
        return self.ser.write(data)

    def reset_input(self) -> None:
        self.ser.reset_input_buffer()

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:  # pragma: no cover - best effort on unplug
            pass


class MspClient:
    """MSP client that speaks v1 and v2 and can run the firmware CLI."""

    def __init__(self, transport, default_timeout: float = 3.0, settle: float = 0.01):
        self.transport = transport
        self.default_timeout = default_timeout
        self.settle = settle
        self._cli_mode: str | None = None
        self.api_version: tuple[int, int, int] | None = None

    # -- low level ---------------------------------------------------------

    def _read_exact(self, size: int, deadline: float) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            if time.monotonic() > deadline:
                raise MspTimeout(
                    f"timed out after {len(chunks)}/{size} bytes "
                    f"(port {getattr(self.transport, 'port', '?')})"
                )
            chunk = self.transport.read(size - len(chunks))
            if chunk:
                chunks += chunk
        return bytes(chunks)

    def _read_frame(self, timeout: float) -> MspFrame:
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                raise MspTimeout(
                    f"no MSP frame within {timeout:.1f}s "
                    f"(port {getattr(self.transport, 'port', '?')})"
                )
            first = self.transport.read(1)
            if first != b"$":
                continue
            marker = self._read_exact(2, deadline)
            kind, direction = marker[0:1], marker[1:2]
            if direction == b"!":
                raise MspCommandError(
                    f"flight controller returned an MSP error for command "
                    f"{'v1' if kind == b'M' else 'v2'}"
                )
            if direction != b">":
                continue
            if kind == b"M":
                return self._read_v1_body(deadline)
            if kind == b"X":
                return self._read_v2_body(deadline)

    def _read_v1_body(self, deadline: float) -> MspFrame:
        size = self._read_exact(1, deadline)[0]
        command = self._read_exact(1, deadline)[0]
        body = self._read_exact(size, deadline)
        checksum = self._read_exact(1, deadline)[0]
        expected = 0
        for byte in bytes([size, command]) + body:
            expected ^= byte
        if checksum != expected:
            raise MspChecksumError(
                f"MSP v1 checksum mismatch for command {command} "
                f"(got 0x{checksum:02x}, expected 0x{expected:02x})"
            )
        return MspFrame(version=1, command=command, payload=body)

    def _read_v2_body(self, deadline: float) -> MspFrame:
        header = self._read_exact(5, deadline)
        flags, command, size = struct.unpack("<BHH", header)
        if size > MSP_V2_MAX_PAYLOAD:
            raise MspError(f"implausible MSP v2 payload size {size}")
        body = self._read_exact(size, deadline)
        checksum = self._read_exact(1, deadline)[0]
        expected = crc8_dvb_s2(header + body)
        if checksum != expected:
            raise MspChecksumError(
                f"MSP v2 checksum mismatch for command 0x{command:04x} "
                f"(got 0x{checksum:02x}, expected 0x{expected:02x})"
            )
        return MspFrame(version=2, command=command, payload=body, flags=flags)

    def _roundtrip(self, frame: bytes, timeout: float) -> MspFrame:
        self.transport.reset_input()
        self.transport.write(frame)
        if self.settle:
            time.sleep(self.settle)
        return self._read_frame(timeout)

    # -- public requests ---------------------------------------------------

    def request_v1(
        self, command: int, payload: bytes = b"", timeout: float | None = None
    ) -> MspFrame:
        return self._roundtrip(
            encode_v1(command, payload), timeout or self.default_timeout
        )

    def request_v2(
        self, command: int, payload: bytes = b"", timeout: float | None = None
    ) -> MspFrame:
        return self._roundtrip(
            encode_v2(command, payload), timeout or self.default_timeout
        )

    def request(
        self, command: int, payload: bytes = b"", timeout: float | None = None
    ) -> MspFrame:
        if command <= MSP_V1_MAX_COMMAND and len(payload) <= MSP_V1_MAX_PAYLOAD:
            return self.request_v1(command, payload, timeout)
        return self.request_v2(command, payload, timeout)

    # -- familiar Betaflight calls ----------------------------------------

    def ping(self) -> tuple[int, int, int]:
        frame = self.request(MSP_API_VERSION)
        if len(frame.payload) < 3:
            raise MspError("MSP_API_VERSION returned a short payload")
        self.api_version = tuple(frame.payload[:3])
        return self.api_version

    def fc_variant(self) -> str:
        return self.request(MSP_FC_VARIANT).payload.decode("ascii", "replace").strip("\x00 ")

    def fc_version(self) -> str:
        payload = self.request(MSP_FC_VERSION).payload
        if len(payload) < 3:
            return "unknown"
        return f"{payload[0]}.{payload[1]}.{payload[2]}"

    def board_info(self) -> dict:
        payload = self.request(MSP_BOARD_INFO).payload
        data = {
            "board": payload[:4].decode("ascii", "replace").strip("\x00"),
            "hardware_revision": None,
            "board_type": None,
        }
        if len(payload) >= 6:
            data["hardware_revision"] = struct.unpack_from("<H", payload, 4)[0]
        if len(payload) >= 7:
            data["board_type"] = payload[6]
        return data

    def build_info(self) -> dict:
        payload = self.request(MSP_BUILD_INFO).payload
        data = {"date": "", "time": "", "git": "", "raw": payload.hex()}
        if len(payload) >= 8:
            data["date"] = payload[0:4].decode("ascii", "replace")
            data["time"] = payload[4:8].decode("ascii", "replace")
            data["git"] = payload[8:15].decode("ascii", "replace").strip("\x00 ") if len(payload) >= 15 else ""
        return data

    def uid(self) -> str:
        return self.request(MSP_UID).payload.hex()

    def craft_name(self) -> str:
        return self.request(MSP_NAME).payload.decode("utf-8", "replace").strip("\x00")

    def analog(self) -> dict:
        payload = self.request(MSP_ANALOG).payload
        if len(payload) < 7:
            return {"raw": payload.hex()}
        vbat, mah, rssi, amps = struct.unpack_from("<BHHH", payload, 0)
        return {
            "voltage_v": round(vbat / 10.0, 2),
            "mah_drawn": mah,
            "rssi_percent": rssi,
            "amperage_a": round(amps / 100.0, 2),
        }

    def battery_state(self) -> dict:
        payload = self.request(MSP_BATTERY_STATE).payload
        if len(payload) < 8:
            return {"raw": payload.hex()}
        cells = payload[0]
        capacity, voltage, mah, amps = struct.unpack_from("<HHHH", payload, 1)
        state = payload[9] if len(payload) > 9 else None
        return {
            "cells": cells,
            "capacity_mah": capacity,
            "voltage_v": round(voltage / 100.0, 2),
            "voltage_per_cell_v": round(voltage / 100.0 / cells, 2) if cells else None,
            "mah_drawn": mah,
            "amperage_a": round(amps / 100.0, 2),
            "state": state,
        }

    def attitude(self) -> dict:
        payload = self.request(MSP_ATTITUDE).payload
        if len(payload) < 6:
            return {"raw": payload.hex()}
        roll, pitch, yaw = struct.unpack_from("<hhh", payload, 0)
        return {
            "roll_deg": round(roll / 10.0, 1),
            "pitch_deg": round(pitch / 10.0, 1),
            "yaw_deg": yaw,
        }

    def motors(self) -> list[int]:
        payload = self.request(MSP_MOTOR).payload
        count = len(payload) // 2
        return list(struct.unpack_from(f"<{count}H", payload, 0)) if count else []

    def rc_channels(self) -> list[int]:
        payload = self.request(MSP_RC).payload
        count = len(payload) // 2
        return list(struct.unpack_from(f"<{count}H", payload, 0)) if count else []

    def box_names(self) -> list[str]:
        raw = self.request(MSP_BOXNAMES).payload.decode("ascii", "replace")
        return [name for name in raw.replace("\x00", "").split(";") if name]

    def status(self) -> dict:
        payload = self.request(MSP_STATUS).payload
        data = {"raw": payload.hex(), "layout": "unknown"}
        if len(payload) < 10:
            return data

        def decode(offset_i2c: int) -> dict:
            cycle = struct.unpack_from("<H", payload, 0)[0]
            i2c = (
                struct.unpack_from("<H", payload, offset_i2c)[0]
                if offset_i2c == 2
                else payload[2]
            )
            sensor_offset = offset_i2c + 2
            sensors = struct.unpack_from("<H", payload, sensor_offset)[0]
            flags = struct.unpack_from("<I", payload, sensor_offset + 2)[0]
            profile = payload[sensor_offset + 6]
            return {
                "cycle_time_us": cycle,
                "i2c_errors": i2c,
                "sensors_mask": sensors,
                "flight_mode_flags": flags,
                "profile": profile,
            }

        # Newer firmware widened the I2C counter to u16; older used u8 with a
        # padding byte. Profile indexes are small, so sanity-check the layout.
        newer = decode(2)
        older = decode(1)
        if 0 <= newer["profile"] <= 8:
            data.update(newer)
            data["layout"] = "u16-i2c"
        elif 0 <= older["profile"] <= 8:
            data.update(older)
            data["layout"] = "u8-i2c"
        else:
            data.update(newer)
        return data

    # -- firmware CLI ------------------------------------------------------

    def cli_command(self, command_line: str, timeout: float = 8.0) -> str:
        """Run one firmware CLI command and return its text output."""
        if self._cli_mode == "legacy":
            return self._cli_legacy(command_line, timeout)
        try:
            text = self._cli_msp2(command_line, timeout)
            self._cli_mode = "msp2"
            return text
        except (MspCommandError, MspTimeout):
            self._enter_legacy_cli()
            self._cli_mode = "legacy"
            return self._cli_legacy(command_line, timeout)

    def _cli_msp2(self, command_line: str, timeout: float) -> str:
        data = command_line.encode("utf-8")
        if not data:
            raise ValueError("empty CLI command")
        collected = bytearray()
        offset = 0
        while True:
            if offset == 0:
                payload = data
            else:
                payload = data + b"\x00" + struct.pack("<H", offset)
            frame = self.request_v2(MSP2_CLI_COMMAND, payload, timeout)
            if len(frame.payload) < 3:
                raise MspError("MSP2_CLI_COMMAND returned a short payload")
            total, flags = struct.unpack_from("<HB", frame.payload, 0)
            window = frame.payload[3:]
            if flags & MSP2_CLI_COMMAND_FLAG_REFUSED:
                raise MspRefused(f"firmware refused CLI command: {command_line}")
            collected += window
            offset = len(collected)
            if not window or offset >= total:
                break
            if offset > 4 * 1024 * 1024:
                raise MspError("CLI output exceeds 4 MiB; aborting")
        return collected.decode("utf-8", "replace")

    def _enter_legacy_cli(self) -> None:
        self.transport.reset_input()
        self.transport.write(encode_v1(MSP_CLI))
        try:
            self._read_frame(1.5)
        except MspError:
            pass
        try:
            self._read_until_prompt(2.0)
        except MspError:
            pass

    def _read_until_prompt(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        buffer = bytearray()
        while time.monotonic() < deadline:
            chunk = self.transport.read(1)
            if not chunk:
                continue
            buffer += chunk
            if buffer.endswith(b"# "):
                break
        if not buffer.endswith(b"# "):
            raise MspTimeout("firmware CLI prompt did not appear")
        return buffer.decode("utf-8", "replace")

    def _cli_legacy(self, command_line: str, timeout: float) -> str:
        self.transport.write(command_line.encode("utf-8") + b"\r\n")
        text = self._read_until_prompt(timeout)
        text = text.replace(command_line, "", 1)
        text = text.rsplit("# ", 1)[0]
        return text.strip("\r\n")

    def close(self) -> None:
        if self._cli_mode == "legacy":
            try:
                self.transport.write(b"exit\r\n")
                time.sleep(0.1)
            except Exception:
                pass
        self.transport.close()


def split_setting(text: str) -> tuple[str, str] | None:
    """Split ``name = value`` from a CLI/setting response."""
    if "=" not in text:
        return None
    name, value = text.split("=", 1)
    return name.strip(), value.strip()
