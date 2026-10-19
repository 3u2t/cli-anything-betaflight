from __future__ import annotations

import re
import struct
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cli_anything.betaflight.core import build as build_core
from cli_anything.betaflight.utils import msp

STM32_VID = 0x0483
STM32_VCP_PID = 0x5740
OTHER_FC_IDS = {
    (0x1209, 0x4D44),
    (0x2E8A, 0x000A),
    (0x1EAF, 0x0003),
}

DESTRUCTIVE_CLI = (
    "defaults",
    "erase",
    "flash_erase",
    "flash_info",
    "save",
    "bl",
    "dfu",
    "msc",
    "factory_reset",
    "reset",
)

READ_ONLY_ALLOWED = {"flash_info"}

SETTING_LINE_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=\s*(.*)$")
AUX_LINE_RE = re.compile(r"^aux\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")
FEATURE_SECTION_RE = re.compile(r"^(Enabled|Available|Unavailable):\s*(.*)$")


def parse_settings(text: str) -> list[dict]:
    settings = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = SETTING_LINE_RE.match(line)
        if match:
            settings.append({"name": match.group(1), "value": match.group(2).strip()})
    return settings


def parse_features(text: str) -> dict:
    result = {"enabled": [], "available": [], "unavailable": []}
    for raw in text.splitlines():
        match = FEATURE_SECTION_RE.match(raw.strip())
        if match:
            key = match.group(1).lower()
            result[key] = [name for name in match.group(2).split() if name]
    return result


def parse_aux(text: str) -> list[dict]:
    modes = []
    for raw in text.splitlines():
        match = AUX_LINE_RE.match(raw.strip())
        if not match:
            continue
        index, mode, channel, start, end, logic = (int(v) for v in match.groups())
        modes.append(
            {
                "index": index,
                "mode_id": mode,
                "aux_channel": channel,
                "start": start,
                "end": end,
                "logic": logic,
            }
        )
    return modes


def restore_commands(text: str) -> list[str]:
    commands = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.split(" ", 1)[0].lower() in {"save", "defaults"}:
            continue
        commands.append(line)
    return commands


def build_motor_payload(motor: int, throttle: int, motor_count: int = 8) -> bytes:
    if not 1 <= motor <= motor_count:
        raise DeviceError(f"motor must be between 1 and {motor_count}")
    if not 1000 <= throttle <= 2000:
        raise DeviceError("throttle must be between 1000 and 2000")
    values = [1000] * motor_count
    values[motor - 1] = throttle
    return struct.pack(f"<{motor_count}H", *values)


def stop_motor_payload(motor_count: int = 8) -> bytes:
    return struct.pack(f"<{motor_count}H", *([1000] * motor_count))


class DeviceError(RuntimeError):
    pass


@dataclass
class PortInfo:
    device: str
    description: str
    manufacturer: str
    vid: int | None
    pid: int | None
    serial_number: str | None

    @property
    def looks_like_fc(self) -> bool:
        if self.vid == STM32_VID and self.pid == STM32_VCP_PID:
            return True
        return (self.vid, self.pid) in OTHER_FC_IDS

    def as_dict(self) -> dict:
        return {
            "device": self.device,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "vid": f"0x{self.vid:04x}" if self.vid is not None else None,
            "pid": f"0x{self.pid:04x}" if self.pid is not None else None,
            "serial_number": self.serial_number,
            "looks_like_flight_controller": self.looks_like_fc,
        }


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as serial_list

    ports = []
    for port in serial_list.comports():
        ports.append(
            PortInfo(
                device=port.device,
                description=port.description or "",
                manufacturer=port.manufacturer or "",
                vid=port.vid,
                pid=port.pid,
                serial_number=port.serial_number,
            )
        )
    return sorted(ports, key=lambda p: p.device)


def pick_port(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    ports = list_ports()
    fc_ports = [p for p in ports if p.looks_like_fc]
    if not fc_ports:
        if len(ports) == 1:
            return ports[0].device
        names = ", ".join(p.device for p in ports) or "none"
        raise DeviceError(
            "No flight controller detected. Serial ports found: "
            f"{names}. Pass --port COMx."
        )
    if len(fc_ports) > 1:
        names = ", ".join(f"{p.device} ({p.description})" for p in fc_ports)
        raise DeviceError(
            f"Multiple flight controllers found: {names}. Pass --port COMx."
        )
    return fc_ports[0].device


class Device:
    def __init__(self, port: str | None = None, baudrate: int = 115200, timeout: float = 3.0):
        self.port_name = pick_port(port)
        self.baudrate = baudrate
        self.transport = msp.SerialTransport(self.port_name, baudrate)
        self.client = msp.MspClient(self.transport, default_timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self.client.close()


    def info(self) -> dict:
        protocol, major, minor = self.client.ping()
        data = {
            "port": self.port_name,
            "api_version": {
                "protocol": protocol,
                "major": major,
                "minor": minor,
            },
            "variant": self.client.fc_variant(),
            "firmware": self.client.fc_version(),
            "craft_name": self.client.craft_name(),
            "uid": self.client.uid(),
        }
        data.update(self.client.board_info())
        data["build"] = self.client.build_info()
        return data

    def status(self) -> dict:
        data = {
            "port": self.port_name,
            "analog": self.client.analog(),
            "attitude": self.client.attitude(),
            "motors": self.client.motors(),
            "rc_channels": self.client.rc_channels(),
        }
        try:
            data["battery"] = self.client.battery_state()
        except msp.MspError:
            data["battery"] = None
        try:
            data["status"] = self.client.status()
        except msp.MspError:
            data["status"] = None
        try:
            data["modes"] = self.active_modes()
        except msp.MspError:
            data["modes"] = []
        return data

    def active_modes(self) -> list[str]:
        names = self.client.box_names()
        status = self.client.status()
        flags = status.get("flight_mode_flags")
        if flags is None:
            return []
        return [name for bit, name in enumerate(names) if flags & (1 << bit)]


    def config_get(self, name: str) -> dict:
        try:
            frame = self.client.request_v2(
                msp.MSP2_CLI_SETTING, name.encode("utf-8")
            )
            text = frame.payload.decode("utf-8", "replace").strip("\x00\r\n")
            parsed = msp.split_setting(text)
            if parsed:
                return {"name": parsed[0], "value": parsed[1], "source": "msp2"}
        except msp.MspError:
            pass
        text = self.client.cli_command(f"get {name}")
        for line in text.splitlines():
            parsed = msp.split_setting(line)
            if parsed and parsed[0] == name:
                return {"name": parsed[0], "value": parsed[1], "source": "cli"}
        raise DeviceError(f"Setting '{name}' not found on the flight controller")

    def config_set(self, name: str, value: str) -> dict:
        payload = f"{name} = {value}".encode("utf-8")
        try:
            frame = self.client.request_v2(msp.MSP2_CLI_SETTING, payload)
            text = frame.payload.decode("utf-8", "replace").strip("\x00\r\n")
        except msp.MspError:
            text = self.client.cli_command(f"set {name} = {value}")
        parsed = msp.split_setting(text)
        return {
            "name": name,
            "value": parsed[1] if parsed else value,
            "persisted": False,
            "source": "msp2",
        }

    def config_diff(self, all_profiles: bool = False) -> str:
        command = "diff all" if all_profiles else "diff"
        return self.client.cli_command(command, timeout=20.0)

    def config_dump(self) -> str:
        return self.client.cli_command("dump", timeout=30.0)

    def cli(self, command_line: str, timeout: float = 10.0) -> str:
        return self.client.cli_command(command_line, timeout=timeout)


    def settings(self, prefix: str = "") -> list[dict]:
        text = self.client.cli_command(f"get {prefix}".strip(), timeout=30.0)
        return parse_settings(text)

    def features(self) -> dict:
        return parse_features(self.client.cli_command("feature"))

    def feature_set(self, name: str, enabled: bool) -> str:
        command = f"feature {name}" if enabled else f"feature -{name}"
        return self.client.cli_command(command)

    def ports(self) -> dict:
        text = self.client.cli_command("serial")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return {
            "raw": lines,
            "setting_claims": parse_settings(text),
            "legacy_lines": [line for line in lines if line.startswith("serial ")],
        }

    def modes(self) -> list[dict]:
        return parse_aux(self.client.cli_command("aux"))

    def mode_set(
        self,
        index: int,
        mode_id: int,
        aux_channel: int,
        start: int,
        end: int,
        logic: int = 0,
    ) -> str:
        return self.client.cli_command(
            f"aux {index} {mode_id} {aux_channel} {start} {end} {logic}"
        )

    def mode_clear(self, index: int) -> str:
        return self.client.cli_command(f"aux {index} 0 0 900 900 0")

    def backup_text(self) -> str:
        info = self.info()
        header = [
            "# Betaflight CLI backup generated by cli-anything-betaflight",
            f"# generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"# variant: {info.get('variant')}",
            f"# firmware: {info.get('firmware')}",
            f"# board: {info.get('board')}",
            f"# craft: {info.get('craft_name')}",
            "",
        ]
        return "\n".join(header) + self.config_dump()

    def restore(self, text: str, timeout: float = 15.0) -> dict:
        commands = restore_commands(text)
        executed: list[str] = []
        errors: list[dict] = []
        consecutive_errors = 0
        for command in commands:
            try:
                output = self.client.cli_command(command, timeout=timeout)
            except msp.MspError as exc:
                errors.append({"command": command, "error": str(exc)})
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    errors.append(
                        {"command": "(rest)", "error": "link lost; restore aborted"}
                    )
                    break
                continue
            consecutive_errors = 0
            executed.append(command)
            lowered = output.lower()
            if "error" in lowered or "invalid" in lowered:
                errors.append({"command": command, "error": output.strip()})
        return {"executed": len(executed), "errors": errors, "commands": executed}


    def motor_test(self, motor: int, throttle: int = 1050, seconds: float = 2.0) -> dict:
        payload = build_motor_payload(motor, throttle)
        self.client.request(msp.MSP_SET_MOTOR, payload)
        try:
            time.sleep(max(0.2, min(seconds, 10.0)))
        finally:
            self.client.request(msp.MSP_SET_MOTOR, stop_motor_payload())
        return {
            "motor": motor,
            "throttle": throttle,
            "seconds": seconds,
            "stopped": True,
        }

    def acc_calibrate(self) -> dict:
        self.client.request(msp.MSP_ACC_CALIBRATION)
        return {"calibrated": True, "note": "keep the craft level and still"}


    def save(self) -> dict:
        self.client.request(msp.MSP_EEPROM_WRITE)
        return {"saved": True, "note": "flight controller reboots"}

    def reboot(self) -> dict:
        self.client.request(msp.MSP_REBOOT)
        return {"rebooted": True}


def is_destructive(command_line: str) -> bool:
    first, _, rest = command_line.strip().partition(" ")
    first = first.lower()
    rest = rest.strip()
    if first in READ_ONLY_ALLOWED:
        return False
    if first in DESTRUCTIVE_CLI:
        return True
    if first == "set":
        return True
    if first in {"feature", "serial", "aux", "beep", "map", "mmix"}:
        return bool(rest)
    return False


def guard_command(command_line: str, force: bool) -> None:
    if force or not is_destructive(command_line):
        return
    raise DeviceError(
        f"'{command_line.split(' ', 1)[0]}' changes the flight controller and is "
        "blocked. Re-run with --force if you really mean it (and remember that "
        "'save' is a separate step)."
    )


def dfu_flash(firmware: Path) -> dict:
    binary = build_core.find_dfu()
    if not binary:
        raise DeviceError(
            "dfu-util is not installed (winget install dfu-util), and the "
            "controller must be in DFU mode."
        )
    firmware = Path(firmware).expanduser()
    if not firmware.is_file():
        raise DeviceError(f"Firmware file not found: {firmware}")
    command = [binary, "-a", "0", "-s", "0x08000000:leave", "-D", str(firmware)]
    result = subprocess.run(command, capture_output=True, text=True)
    return {
        "command": command,
        "returncode": result.returncode,
        "success": result.returncode == 0,
        "output": (result.stdout or "") + (result.stderr or ""),
    }


def dfu_list_devices() -> dict:
    binary = build_core.find_dfu()
    if not binary:
        raise DeviceError("dfu-util is not installed (winget install dfu-util)")
    result = subprocess.run([binary, "-l"], capture_output=True, text=True)
    return {
        "returncode": result.returncode,
        "output": (result.stdout or "") + (result.stderr or ""),
    }


def wait_for_port(port: str, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(p.device == port for p in list_ports()):
            return True
        time.sleep(0.3)
    return False


def describe_connection_error(exc: Exception) -> str:
    text = str(exc)
    if "Access is denied" in text or "PermissionError" in type(exc).__name__:
        return (
            f"{text}\nClose Betaflight Configurator (it holds the port) and retry."
        )
    return text
