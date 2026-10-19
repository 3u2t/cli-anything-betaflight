# Architecture

The tool has three parts: reading the firmware source, building/flashing, and
talking to a controller.

```
cli_anything/betaflight/
  betaflight_cli.py     Click commands, REPL, JSON output, safety checks
  core/source.py        config.h parser, board index, validation, diff
  core/build.py         toolchain lookup, make runner, artifacts, flashing
  core/device.py        serial ports, MSP actions, backup/restore, DFU
  utils/msp.py          MSP v1/v2 framing and CLI-over-MSP transport
  utils/repl_skin.py    REPL skin from CLI-Anything (Apache-2.0)
```

## Source

`source.py` reads `src/config/configs/<MANUFACTURER>/<BOARD>/config.h` and the
older flat layout. Comments and function-like macros are ignored. The board
index exposes pins (`*_PIN`), features (`USE_*`), timer mappings and the MCU.

Validation reports name and manufacturer mismatches, duplicate board names,
malformed MCUs, missing MCUs and boards that still use the flat layout.

## Builds

`build.py` runs the firmware Makefile:

```
make CONFIG=<BOARD> [-jN] [OPTIONS=...]
```

On Windows it finds MSYS2 and calls `make` through `bash -lc`, because the
Makefile expects a POSIX shell. Paths are converted from `C:\...` to `/c/...`.

Betaflight rejects spaces in the checkout path. If the path contains a space,
the tool uses `C:\betaflight` or `~/betaflight` automatically when either one
points at the same directory.

The ARM toolchain is the one installed by `make arm_sdk_install`
(GCC 13.3.1). Flashing calls the existing `dfu_flash` and `tty_flash` targets.

## Device access

Ports come from pyserial. STM32 VCP devices (VID `0x0483`, PID `0x5740`) are
marked as flight controllers.

MSP v1 and v2 are implemented directly. Commands above `0xFF` use v2. CLI text
is sent through `MSP2_CLI_COMMAND`; firmware without it uses `MSP_CLI` (217)
and the old text prompt.

Structured commands cover settings, features, ports, AUX modes,
backup/restore, accelerometer calibration and motor test. Other firmware
commands are available through `device cli`.

## Safety

Commands that write to the controller require `--yes`. `config set` and
`features` write to RAM only; `device save` persists them. `device cli`
rejects `defaults`, `save`, `set`, `serial`, `feature` and similar commands
unless `--force` is given.

Motor test runs for at most 10 seconds and always sends a stop command in a
`finally` block. Restore strips `save` and `defaults` from the dump and stops
after three consecutive link errors.
