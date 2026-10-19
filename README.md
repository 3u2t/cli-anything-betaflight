# cli-anything-betaflight

An agent-native CLI for the [Betaflight](https://github.com/betaflight/betaflight)
firmware source tree and for the flight controllers running it.

It was generated with the
[CLI-Anything](https://github.com/HKUDS/CLI-Anything) 7-phase methodology:
source analysis → command design → implementation → test plan → tests →
documentation → packaging.

## What it does

* **Source intelligence** — parses every board in `src/config/configs`
  (600+ boards, 140+ manufacturers): MCU, features, pin map, timer mapping.
  `boards diff` compares two boards; `boards validate` lints names, IDs,
  duplicate boards and malformed MCUs.
* **Build & flash orchestration** — wraps the real firmware makefile
  (`make CONFIG=<BOARD>`), discovers hex/bin/elf/dfu artifacts, and flashes via
  DFU or serial with explicit confirmation.
* **Live controller access** — MSP v1/v2 over USB serial: firmware/board/UID
  info, voltage/attitude/motors/RC/modes, settings, features, ports, AUX modes,
  `get`/`set`, `diff`/`dump`, backup/restore, motor test, accelerometer
  calibration, and arbitrary firmware CLI commands with a destructive guard.
* **GUI parity layer** — one command per Configurator tab: Setup, Ports,
  Configuration, Power, Failsafe, PID/Rates/Filters, Receiver, Modes, Motors,
  OSD, VTX, Blackbox, CLI, Firmware Flasher, Backup/Restore. Not covered yet:
  the OSD drag-and-drop editor, online presets, cloud build, blackbox log
  download and live sensor graphs.

Everything supports `--json` for agents and a REPL for interactive work.

## Install

From GitHub:

```bash
pip install "git+https://github.com/3u2t/cli-anything-betaflight.git#subdirectory=agent-harness"
```

From a local checkout:

```bash
cd agent-harness
pip install -e .
```

Requires Python 3.10+, `click`, `prompt-toolkit`, `pyserial`.

## Point it at a checkout

```bash
# auto-detect, or:
export BETAFLIGHT_REPO=C:/betaflight
cli-anything-betaflight --repo C:/betaflight repo info
```

Board configs live in a submodule:

```bash
git submodule update --init src/config
```

## Build & flash on Windows

Betaflight's makefiles reject spaces in the repository path and need a POSIX
shell. Keep the checkout where it is and add a space-free junction:

```powershell
cmd /c mklink /J C:\betaflight "<path-to-checkout>"
```

Toolchain installed on this machine:

| Tool | Location |
|------|----------|
| MSYS2 + make | `C:\msys64\usr\bin\make.exe` |
| ARM GNU GCC 13.3.1 | `%USERPROFILE%\Documents\tools\arm-gnu-toolchain-13.3.rel1-*` |
| dfu-util 0.11 | `C:\Tools\dfu-util-0.11-binaries\win64\dfu-util.exe` |

`make arm_sdk_install` downloaded the exact GCC version Betaflight requires
(`13.3.1`). The harness detects MSYS2 itself and runs `make` through its bash,
so no MSYS2 shell is needed. Passing the spaced path still works: the
`C:\betaflight` junction is picked up automatically.

Verified build (2026-10-20):

```text
build ok: ACCTON_GFF4 2026.12.0-alpha
  betaflight_2026.12.0-alpha_STM32F405_ACCTON_GFF4.hex    1.48 MB
  betaflight_STM32F405_ACCTON_GFF4.elf                    776.8 KB
FLASH1: 550945 B / 992 KB (54.24%), RAM: 95060 B / 128 KB (72.53%)
```

## Quick start

```bash
cli-anything-betaflight repo info
cli-anything-betaflight boards list --mcu STM32F405
cli-anything-betaflight boards show ACCTON_GFF4
cli-anything-betaflight boards validate --errors-only
cli-anything-betaflight build doctor
cli-anything-betaflight build run ACCTON_GFF4 -j8
cli-anything-betaflight build artifacts ACCTON_GFF4

cli-anything-betaflight device list
cli-anything-betaflight device info --port COM4
cli-anything-betaflight device status --port COM4
cli-anything-betaflight device config diff --port COM4
cli-anything-betaflight device features --port COM4
cli-anything-betaflight device modes --port COM4
cli-anything-betaflight device settings --prefix gyro --port COM4

# backup before a change, restore if it goes wrong
cli-anything-betaflight device backup quad.txt --port COM4
cli-anything-betaflight device restore quad.txt --yes --port COM4

# motors and calibration are guarded (props off!)
cli-anything-betaflight device motor --motor 1 --throttle 1050 --seconds 2 --yes
cli-anything-betaflight device acc-calibrate --yes
```

## Tests

```bash
pip install pytest
pytest cli_anything/betaflight/tests -q
# 59 passed, 4 skipped (the 4 are live-hardware tests)
# hardware, read-only:
set BETAFLIGHT_E2E_PORT=COM4
pytest cli_anything/betaflight/tests/test_full_e2e.py -q
```

## Safety

`config set`, `save`, `reboot`, `build flash`, `build clean`, `device flash`,
`device restore`, `device motor` and `device acc-calibrate` require `--yes`.
`device cli` refuses mutating commands (`defaults`, `save`, `set`, `serial`,
`feature`, …) unless `--force` is passed. Reads never change the controller.
