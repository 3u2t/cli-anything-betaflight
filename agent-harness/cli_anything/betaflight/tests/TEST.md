# TEST.md â€” cli-anything-betaflight

## Test inventory

| File | Scope | Planned |
|------|-------|---------|
| `test_core.py` | MSP v1/v2 framing, paging, CLI fallback, config.h parsing, board index, validation, diff | 27 |
| `test_cli.py` | Click surface: boards/repo/build/device dispatch, `--json`, guards | 18 |
| `test_device.py` | settings/feature/aux parsers, motor payloads, restore filtering, write guards | 10 |
| `test_build.py` | MSYS2 path conversion, toolchain-status shape, space/alias detection | 4 |
| `test_full_e2e.py` | Live flight controller over MSP (opt-in via `BETAFLIGHT_E2E_PORT`) | 4 |

## Unit tests (synthetic data, no hardware)

* Framing
  * `encode_v1` emits `$M< size cmd payload xor`.
  * `encode_v2` emits `$X< flag func size payload crc8`.
  * `crc8_dvb_s2(b"123456789") == 0xBC`.
  * Bad checksums raise `MspChecksumError`; `$M!` raises `MspCommandError`;
    silence raises `MspTimeout`.
* CLI transport
  * `MSP2_CLI_COMMAND` output is paged with `name\0offset` and concatenated.
  * `MSP2_CLI_COMMAND_FLAG_REFUSED` raises `MspRefused`.
  * Older firmware: MSP2 request fails, `MSP_CLI` (217) fallback switches to raw
    text, executes one command, strips echo/prompt.
* Source parsing
  * Comments (`//`, `/* */`) are ignored; function-like macros are skipped.
  * `configs/<MFR>/<BOARD>/config.h` and flat layouts both load.
  * Validation catches: missing `FC_TARGET_MCU`, `BOARD_NAME` mismatch,
    `MANUFACTURER_ID` mismatch, duplicate board names, malformed MCU.
  * `diff_boards` reports added/removed/changed defines with categories.

## End-to-end tests (real software)

* Real source tree (the local Betaflight checkout):
  * `repo info` reports the git commit and 600+ boards.
  * `boards list` returns a non-empty table and valid JSON.
  * `boards show` for a real board exposes MCU and pin defines.
  * `boards validate` exits 0 and only reports expected severities.
  * `build run --dry-run` prints the make command without building.
* Hardware (opt-in, read-only):
  * `device list` marks STM32 VCP ports.
  * `device info` returns variant, firmware, board and UID.
  * `device status` returns voltage/attitude/motors/RC.
  * `device config diff` returns CLI text.
  * All write commands (`config set`, `save`, `reboot`, `cli --force`) are
    verified to demand `--yes`/`--force` *before* touching the port.

## Safety checks

* `device config set` refuses without `--yes`.
* `device features --enable/--disable` refuses without `--yes`.
* `device restore`, `device motor`, `device acc-calibrate`, `device flash`
  refuse without `--yes` **before touching the port** (verified with a poisoned
  `Device` factory).
* `device save` / `device reboot` refuse without `--yes`.
* `device cli defaults|save|erase|set ...` is blocked without `--force`.
* `build flash` refuses without `--yes`.
* `build clean` refuses without `--yes`.

## Status

Implemented and run on 2026-10-20:

```
59 passed, 4 skipped in 10.41s
```

The four skips are the live-controller tests (opt-in via `BETAFLIGHT_E2E_PORT`).
The two STM32 VCP ports present on this machine (COM4/COM5) are ghost entries:
`Get-CimInstance Win32_SerialPort` is empty and pyserial lists no ports, so no
controller was attached during the run.

Verified against the real checkout (634 boards, firmware 2026.12.0-alpha):
`repo info`, `repo doctor`, `boards list/show/diff/validate/mcus`,
`build run`, `build clean`, `build artifacts`, `device list`.

A full clean build was executed through the CLI
(`build clean ACCTON_GFF4 --yes` â†’ `build run ACCTON_GFF4 -j8`) with MSYS2
make and ARM GNU GCC 13.3.1:

```
Linking STM32F405_ACCTON_GFF4
FLASH1: 550945 B / 992 KB (54.24%)
RAM:    95060 B / 128 KB (72.53%)
build ok: ACCTON_GFF4 2026.12.0-alpha
  betaflight_2026.12.0-alpha_STM32F405_ACCTON_GFF4.hex    1.48 MB
  betaflight_STM32F405_ACCTON_GFF4.elf                    776.8 KB
```

`boards validate --errors-only` reports one genuine upstream finding:

```
[error] BROT/BROTHERHOBBYF405V2: BOARD_NAME is 'BHF405V2' but the directory is
        'BROTHERHOBBYF405V2' (board-name-mismatch)
```

DFU flashing is implemented but not exercised: no controller was in DFU mode
during the run (`device dfu-list` returns just the dfu-util banner).

