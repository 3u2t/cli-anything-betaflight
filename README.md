# cli-anything-betaflight

A command-line tool for [Betaflight](https://github.com/betaflight/betaflight).
It works with the firmware source tree (board definitions, builds) and with a
connected flight controller over MSP.

## Requirements

- Python 3.10+
- A Betaflight checkout with the `src/config` submodule initialized
- For builds: GNU make and ARM GNU GCC 13.3.1 (on Windows: MSYS2)
- For DFU flashing: dfu-util

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

## Set up the source tree

```bash
git submodule update --init src/config
export BETAFLIGHT_REPO=C:/betaflight
```

`--repo PATH` overrides the environment variable. Without either, the tool
looks for a checkout in the current directory and its parents.

## Commands

### Boards

```bash
cli-anything-betaflight repo info
cli-anything-betaflight boards list --mcu STM32F405
cli-anything-betaflight boards show ACCTON_GFF4
cli-anything-betaflight boards diff ACCTON_GFF4 SPEEDYBEEF405V3
cli-anything-betaflight boards validate --errors-only
```

### Build

```bash
cli-anything-betaflight build doctor
cli-anything-betaflight build run ACCTON_GFF4 -j8
cli-anything-betaflight build artifacts ACCTON_GFF4
cli-anything-betaflight build flash ACCTON_GFF4 --yes
cli-anything-betaflight build clean ACCTON_GFF4 --yes
```

### Device

```bash
cli-anything-betaflight device list
cli-anything-betaflight device info --port COM4
cli-anything-betaflight device status --port COM4
cli-anything-betaflight device settings --prefix gyro --port COM4
cli-anything-betaflight device features --port COM4
cli-anything-betaflight device modes --port COM4
cli-anything-betaflight device config diff --port COM4
```

Backup and restore:

```bash
cli-anything-betaflight device backup quad.txt --port COM4
cli-anything-betaflight device restore quad.txt --yes --port COM4
```

Motor test and accelerometer calibration:

```bash
cli-anything-betaflight device motor --motor 1 --throttle 1050 --seconds 2 --yes
cli-anything-betaflight device acc-calibrate --yes
```

Every command also supports `--json`. Run without arguments for the REPL.

## Windows builds

Betaflight's makefiles do not accept spaces in the repository path. If the
checkout is under a path with spaces, create a junction:

```powershell
cmd /c mklink /J C:\betaflight "C:\path\to\betaflight"
```

`build` looks for MSYS2 in `C:\msys64` and runs `make` through its bash, so no
separate MSYS2 window is needed. The required ARM toolchain can be installed
with:

```bash
make arm_sdk_install
```

## Safety

These commands change the controller and require `--yes`:

- `device config set`
- `device features --enable/--disable`
- `device restore`
- `device motor`
- `device acc-calibrate`
- `device flash`
- `device save`
- `device reboot`
- `build flash`
- `build clean`

`device cli` blocks mutating firmware commands unless `--force` is passed.
Settings written with `config set` stay in RAM until `device save`.

## Tests

```bash
pip install pytest
pytest cli_anything/betaflight/tests -q
```

With a controller attached (read-only):

```bash
set BETAFLIGHT_E2E_PORT=COM4
pytest cli_anything/betaflight/tests/test_full_e2e.py -q
```
