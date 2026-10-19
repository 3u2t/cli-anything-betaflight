---
name: cli-anything-betaflight
description: >
  Command-line tool for the Betaflight flight-controller firmware. Inspect the
  source tree (boards, MCUs, pin maps, features), build and flash firmware from
  source, and read or change a connected controller over MSP. Use it for
  Betaflight board configs, firmware builds, or flight-controller settings.
---

# cli-anything-betaflight

A command-line tool for the [Betaflight](https://github.com/betaflight/betaflight)
source tree and the flight controllers it runs on.

## Installation

```bash
cd agent-harness
pip install -e .
```

Requires Python 3.10+, `click`, `prompt-toolkit` and `pyserial`. A Betaflight
checkout must exist somewhere; pass `--repo PATH` or set `BETAFLIGHT_REPO`.
The board definitions live in the `src/config` submodule:

```bash
git submodule update --init src/config
```

## Global options

```
--repo PATH     Betaflight checkout (auto-detected from the cwd)
--port COMx     Serial port (auto-detected for STM32 VCP devices)
--baud 115200   MSP baudrate
--timeout 3.0   MSP timeout in seconds
--json          machine-readable output
```

Everything works as a one-shot command or in the REPL (run without arguments).

## Command groups

### repo

| Command | Purpose |
|---------|---------|
| `repo info` | path, git branch/commit, board counts, firmware version |
| `repo doctor` | make/ARM GCC/dfu-util, submodule, path-with-space checks |

### boards

| Command | Purpose |
|---------|---------|
| `boards list [--search Q] [--mcu M] [--manufacturer ID]` | list boards |
| `boards show BOARD [--pins/--no-pins]` | identity, features, pin map |
| `boards diff BOARD_A BOARD_B [--category mcu\|pin\|feature\|timer\|setting]` | config diff |
| `boards validate [BOARD] [--all] [--errors-only]` | lint names, IDs, MCU, duplicates |
| `boards mcus` / `boards manufacturers` | distribution counts |

### build

| Command | Purpose |
|---------|---------|
| `build doctor` | toolchain readiness |
| `build run BOARD [-j N] [--options "..."] [--dry-run]` | `make CONFIG=BOARD` |
| `build artifacts BOARD` | hex/bin/elf/dfu/zip with sizes |
| `build clean [BOARD] --yes` | remove build products |
| `build flash BOARD --method dfu\|serial --yes` | flash firmware |

> Betaflight's makefiles reject paths containing a space. The tool detects
> MSYS2 (`C:\msys64`), runs `make` through its bash, and automatically uses a
> space-free junction (`C:\betaflight`) when the checkout itself is under a
> spaced path. The exact ARM toolchain (GCC 13.3.1) is installed with
> `make arm_sdk_install`.

### device

| Command | Purpose |
|---------|---------|
| `device list` | serial ports, marking STM32 VCP flight controllers |
| `device info` | variant, firmware, board, UID, craft name, build |
| `device status` | voltage, current, attitude, motors, RC, active modes |
| `device settings [--prefix P]` | every firmware setting as `name = value` |
| `device features [--enable\|--disable NAME --yes]` | feature list / toggle |
| `device ports` | UART/USB port assignments (`serial`) |
| `device modes` | AUX mode ranges (`aux`) |
| `device backup FILE` | full CLI dump with metadata header |
| `device restore FILE --yes [--save]` | replay a backup |
| `device motor --motor N --yes` | spin one motor (props off, max 10 s) |
| `device acc-calibrate --yes` | accelerometer calibration |
| `device config get NAME` | read one setting (`MSP2_CLI_SETTING`, CLI fallback) |
| `device config set NAME VALUE --yes` | write to RAM only |
| `device config diff [--all]` | firmware `diff`/`diff all` |
| `device config dump` | firmware `dump` |
| `device cli CMD... [--force]` | run a firmware CLI command |
| `device flash FILE --yes` | flash .hex/.bin over DFU |
| `device dfu-list` | list controllers in DFU mode |
| `device save --yes` | persist settings (controller reboots) |
| `device reboot --yes` | reboot |

## GUI tab map

| Betaflight Configurator tab | CLI equivalent |
|-----------------------------|----------------|
| Setup (info, sensors, attitude) | `device info`, `device status` |
| Ports | `device ports` |
| Configuration (features, beeper, arming) | `device features`, `device settings --prefix beeper_` / `--prefix arming` |
| Power & Battery | `device settings --prefix battery` / `--prefix vbat` |
| Failsafe | `device settings --prefix failsafe` |
| PID Tuning (PID/Rates/Filters) | `device settings --prefix pid_` / `--prefix <rate>_` / `--prefix gyro_lpf` |
| Receiver | `device settings --prefix rx_`, `device cli rc` |
| Modes | `device modes`, `device cli aux ... --force` |
| Adjustments | `device settings --prefix adjustment` |
| Motors (test, protocol) | `device motor`, `device settings --prefix motor` |
| OSD | `device settings --prefix osd_` |
| Video Transmitter | `device settings --prefix vtx_` |
| Sensors | `device cli status`, `device settings --prefix sensor` |
| Blackbox | `device settings --prefix blackbox` |
| CLI | `device cli ...` |
| Firmware Flasher | `build run`, `build flash`, `device flash`, `device dfu-list` |
| Backup / Restore | `device backup`, `device restore` |

Not covered yet: the OSD element drag-and-drop editor, the online presets
library, the cloud-build service, blackbox log download and live sensor graphs.


## Safety model

Write paths are opt-in and never persist by accident:

* `device config set`, `device features --enable/--disable`, `device motor`,
  `device acc-calibrate` and `device restore` all require `--yes`.
* `device config set` and `device features` write to RAM; persisting requires a
  separate `device save --yes` (or `device restore --save`).
* `device cli` blocks `defaults`, `save`, `erase`, `flash_erase`, `set`,
  `feature` (with arguments), `serial` (with arguments), `aux` (with arguments),
  `map`, `mmix`, `beep`, `bl`, `dfu`, `msc` unless `--force` is given.
* `build flash`, `build clean`, `device save`, `device reboot`,
  `device flash` all require `--yes`.
* Motor test stops the motor in a `finally` block even if the read fails.

## Workflow

Read first, change later:

```bash
# 1. What is in the tree?
cli-anything-betaflight --json repo info
cli-anything-betaflight --json boards list --mcu STM32F405
cli-anything-betaflight --json boards show ACCTON_GFF4

# 2. Is it buildable, and can it build?
cli-anything-betaflight build doctor
cli-anything-betaflight build run ACCTON_GFF4 -j8

# 3. What does the connected controller say?
cli-anything-betaflight device info
cli-anything-betaflight device status
cli-anything-betaflight device config diff
cli-anything-betaflight device modes

# 4. Back up before changing anything
cli-anything-betaflight device backup quad-2026-10-20.txt

# 5. Make a change (explicit), then persist
cli-anything-betaflight device config set gyro_lpf1_static_hz 250 --yes
cli-anything-betaflight device save --yes

# 6. Restore if needed
cli-anything-betaflight device restore quad-2026-10-20.txt --yes --save
```

Always prefer `--json` for parsing. MSP reads do not modify the controller;
writes are guarded as described above.

## Notes and limits

* `device` currently talks MSP v1/v2 over USB serial (115200 8N1). It uses
  `MSP2_CLI_COMMAND` when the firmware supports it and falls back to legacy
  `MSP_CLI` (217) for older releases.
* Flashing through DFU needs `dfu-util`; serial flashing needs `stm32flash`.
* Building is verified on this machine: MSYS2 `make`, ARM GNU GCC 13.3.1
  (`make arm_sdk_install`), dfu-util 0.11. A clean `build run ACCTON_GFF4`
  produced a 1.48 MB hex and a 776.8 KB elf.
* Board parsing reads `src/config/configs/<MFR>/<BOARD>/config.h` (and the
  legacy flat layout).
