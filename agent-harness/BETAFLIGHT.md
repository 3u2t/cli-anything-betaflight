# BETAFLIGHT.md — Agent Harness SOP

Standard operating procedure for `cli-anything-betaflight`, following the
CLI-Anything harness methodology.

## Purpose

Give coding agents a single CLI to work with Betaflight end to end:

1. understand the firmware source tree (boards, MCUs, pin maps, features),
2. build and flash firmware from that tree,
3. read and change a connected flight controller over MSP.

## Source of truth

| What | Where |
|------|-------|
| Firmware version | `src/main/build/version.h` |
| Board definitions | `src/config/configs/<MANUFACTURER>/<BOARD>/config.h` (legacy flat layout also supported) |
| Build system | root `Makefile`, `mk/*.mk`, `src/platform/*/mk` |
| CLI command set | `src/main/cli/cli.c` |
| MSP protocol | `src/main/msp/msp_protocol.h` |

Nothing in the harness duplicates firmware logic: it parses the real files and
calls the real `make` targets and MSP commands.

## Architecture

```
agent-harness/
  cli_anything/betaflight/
    betaflight_cli.py     Click groups, REPL, --json, safety guards
    core/source.py        config.h parser, BoardIndex, validation, diff
    core/build.py         toolchain discovery, MSYS2 make runner, artifacts, flash
    core/device.py        port detection, MSP actions, backup/restore, DFU
    utils/msp.py          MSP v1/v2 framing, CLI-over-MSP paging, legacy fallback
    utils/repl_skin.py    unmodified CLI-Anything REPL skin
    skills/SKILL.md       packaged skill copy
    tests/                unit + opt-in hardware E2E tests
```

Command groups mirror the domains, not the GUI widgets:

| Group | Domain |
|-------|--------|
| `repo` | checkout state, toolchain and submodule readiness |
| `boards` | board inventory, details, diff, lint |
| `build` | `make CONFIG=<BOARD>`, artifacts, clean, flash |
| `device` | MSP reads/writes, backup/restore, motor test, DFU |

## Source layer

* `parse_defines()` strips comments and function-like macros, then keeps object
  macros.
* `BoardIndex` loads grouped and flat layouts, indexes by name, and exposes
  pins (`*_PIN`), features (`USE_*`) and timer mappings.
* `validate()` reports `board-name-mismatch`, `manufacturer-mismatch`,
  `mcu-format`, `duplicate-name`, `flat-layout` and missing-MCU issues.
* `diff_boards()` categorizes changes as `mcu`, `pin`, `feature`, `timer` or
  `setting`.

## Build layer

`build.py` never reimplements compilation. It locates the toolchain, then runs
the firmware Makefile:

```
make CONFIG=<BOARD> [-jN] [OPTIONS=...]
```

Platform handling:

* **Windows** — detects MSYS2 (`C:\msys64`, `BETAFLIGHT_MSYS2` override) and
  runs `make` through `bash -lc`, because the Makefile needs a POSIX shell.
  Paths are converted to `/c/...`.
* **Space-free alias** — Betaflight rejects whitespace in `CONFIG_DIR`. If the
  checkout path contains a space, `C:\betaflight` (or `~/betaflight`) is used
  automatically when it points at the same tree.
* **ARM toolchain** — `make arm_sdk_install` downloads the exact required
  release (currently GCC 13.3.1); the harness also finds it under
  `~/Documents/tools/arm-gnu-toolchain-*`.
* **Flashing** — `make dfu_flash` / `make tty_flash`, with `dfu-util` and
  `stm32flash` discovered on `PATH` or in `C:\Tools`.

## Device layer

* Ports are detected with pyserial; STM32 VCP (VID `0x0483`, PID `0x5740`) is
  marked as a flight controller.
* MSP v1 and v2 are implemented directly. Commands above `0xFF` use v2.
* CLI text runs through `MSP2_CLI_COMMAND` (paged with name/offset). Firmware
  without it falls back to legacy `MSP_CLI` (217) plus raw prompt handling.
* Structured wrappers exist for settings, features, ports, AUX modes,
  backup/restore, accelerometer calibration and motor test.
* Everything else remains reachable through `device cli`, which blocks
  mutating commands unless `--force` is given.

## Safety model

* `config set`, `features --enable/--disable`, `restore`, `motor`,
  `acc-calibrate`, `flash`, `save`, `reboot` require `--yes`.
* RAM and flash are separate: `config set`/`features` never persist without a
  following `save --yes`.
* Motor test spins one motor for at most 10 s and always sends a stop in a
  `finally` block. Props must be removed.
* `build clean` requires `--yes`.
* Restore strips `save` and `defaults` from the dump and aborts after three
  consecutive link errors.

## Testing

| Layer | File | Backend needed |
|-------|------|----------------|
| MSP framing, paging, legacy CLI | `test_core.py` | no |
| config.h parsing, validation, diff | `test_core.py` | no |
| Click surface and guards | `test_cli.py` | no |
| settings/mode parsers, motor payloads | `test_device.py` | no |
| toolchain discovery, MSYS2 paths | `test_build.py` | no |
| live controller (read-only) | `test_full_e2e.py` | FC on `BETAFLIGHT_E2E_PORT` |

Run:

```bash
python -m pytest cli_anything/betaflight/tests -q
```

## Packaging and skill

* `setup.py` exposes `cli-anything-betaflight`.
* Canonical skill: `skills/cli-anything-betaflight/SKILL.md`.
* Packaged copy: `cli_anything/betaflight/skills/SKILL.md`.
* `utils/repl_skin.py` is an unmodified copy of the CLI-Anything skin.

## Limits

* No OSD drag-and-drop editor, no online presets, no cloud build, no blackbox
  log download, no live sensor graphs.
* DFU/serial flashing is implemented but only build-time verified here; no
  controller was attached during the reference run.
* Firmware compatibility follows the MSP and CLI implementations of the
  checkout being used; very old releases may lack MSP2 CLI and some settings.
