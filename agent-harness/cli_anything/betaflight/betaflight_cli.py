from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import click

from cli_anything.betaflight.core import build as build_core
from cli_anything.betaflight.core import device as device_core
from cli_anything.betaflight.core import source as source_core
from cli_anything.betaflight.utils import msp
from cli_anything.betaflight.utils.repl_skin import ReplSkin

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
VERSION = "1.0.0"

COMMANDS = {
    "repo info": "checkout path, git state, board counts",
    "repo doctor": "toolchain, submodule and path checks",
    "boards list": "list boards (--search/--mcu/--manufacturer)",
    "boards show": "identity, features and pin map of one board",
    "boards diff": "compare two board configs",
    "boards validate": "lint one board or the whole tree",
    "boards mcus": "board counts per MCU",
    "build run": "compile firmware with make (streams output)",
    "build artifacts": "list built hex/bin/elf files",
    "build flash": "flash firmware (dfu or serial)",
    "build clean": "remove build products",
    "device list": "serial ports, marking flight controllers",
    "device info": "firmware, board, UID and craft name over MSP",
    "device status": "voltage, attitude, motors, RC and active modes",
    "device settings": "list firmware settings (--prefix to filter)",
    "device features": "list features, --enable/--disable NAME --yes",
    "device ports": "UART/USB port assignments",
    "device modes": "AUX mode ranges",
    "device backup": "write a CLI dump backup to a file",
    "device restore": "replay a backup (--yes, optional --save)",
    "device motor": "spin one motor briefly (--yes, props off)",
    "device acc-calibrate": "level the craft, then calibrate (--yes)",
    "device config get": "read one firmware setting",
    "device config set": "write a setting to RAM (--yes, then save)",
    "device config diff": "firmware 'diff all' output",
    "device config dump": "firmware 'dump' output",
    "device cli": "run a firmware CLI command (guarded)",
    "device flash": "flash a .hex/.bin over DFU (--yes)",
    "device dfu-list": "list controllers in DFU mode",
    "device save": "write settings to flash (--yes)",
    "device reboot": "reboot the flight controller (--yes)",
}


def _emit(ctx: click.Context, data, lines: list[str] | None = None) -> None:
    if ctx.obj.get("json"):
        click.echo(json.dumps(data, indent=2, default=str))
        return
    if lines is None:
        if isinstance(data, dict):
            lines = [f"{key}: {value}" for key, value in data.items()]
        elif isinstance(data, list):
            lines = [str(item) for item in data]
        else:
            lines = [str(data)]
    for line in lines:
        click.echo(line)


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(
            "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        )
    return lines


def _get_index(ctx: click.Context) -> source_core.BoardIndex:
    return source_core.BoardIndex(ctx.obj["repo"])


def _get_board(index: source_core.BoardIndex, name: str) -> source_core.Board:
    try:
        return index.get(name)
    except source_core.RepoError as exc:
        raise click.ClickException(str(exc)) from exc


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.2f} MB"
    return f"{size / 1024:.1f} KB"


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--repo", "repo_path", default=None, type=click.Path(),
              help="Betaflight checkout (default: auto-detect)")
@click.option("--port", default=None, help="Serial port of the flight controller")
@click.option("--baud", default=115200, type=int, show_default=True,
              help="Serial baudrate for MSP")
@click.option("--timeout", "msp_timeout", default=3.0, type=float, show_default=True,
              help="MSP request timeout in seconds")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Machine-readable JSON output")
@click.version_option(VERSION, prog_name="cli-anything-betaflight")
@click.pass_context
def cli(ctx: click.Context, repo_path, port, baud, msp_timeout, as_json):
    """cli-anything-betaflight - inspect, build and flash Betaflight."""
    ctx.ensure_object(dict)
    ctx.obj.update(
        {
            "json": as_json,
            "port": port,
            "baud": baud,
            "msp_timeout": msp_timeout,
        }
    )
    try:
        ctx.obj["repo"] = source_core.find_repo(repo_path)
        ctx.obj["repo_error"] = None
    except source_core.RepoError as exc:
        ctx.obj["repo"] = None
        ctx.obj["repo_error"] = str(exc)

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


def _require_repo(ctx: click.Context) -> Path:
    if ctx.obj.get("repo") is None:
        raise click.ClickException(ctx.obj.get("repo_error") or "No Betaflight checkout")
    return ctx.obj["repo"]


@cli.group()
def repo():
    """Inspect the Betaflight checkout."""


@repo.command("info")
@click.pass_context
def repo_info(ctx):
    """Show path, git state and how many boards are available."""
    root = _require_repo(ctx)
    data = source_core.repo_summary(root)
    git = data["git"]
    alias = build_core.space_free_alias(root) if data["path_has_space"] else None
    if not data["path_has_space"]:
        path_note = "ok"
    elif alias:
        path_note = f"contains spaces; builds use {alias}"
    else:
        path_note = "contains spaces - builds will fail"
    lines = [
        f"root:          {data['root']}",
        f"branch:        {git['branch'] or 'unknown'}",
        f"commit:        {git['commit'] or 'unknown'}",
        f"describe:      {git['describe'] or 'unknown'}",
        f"dirty files:   {git['dirty_files']}",
        f"config submodule: {'ready' if git['config_submodule_initialized'] else 'MISSING (git submodule update --init src/config)'}",
        f"boards:        {data['board_count']}",
        f"manufacturers: {data['manufacturer_count']}",
        f"mcus:          {data['mcu_count']}",
        f"version:       {build_core.firmware_version(root)}",
        f"path warning:  {path_note}",
    ]
    _emit(ctx, data, lines)


@repo.command("doctor")
@click.pass_context
def repo_doctor(ctx):
    """Check toolchain, submodule and path prerequisites."""
    root = _require_repo(ctx)
    status = build_core.toolchain_status(root)
    summary = source_core.repo_summary(root)
    problems: list[str] = []
    if not status["make"]["path"]:
        problems.append("make not found")
    if not status["arm_none_eabi_gcc"]["path"]:
        problems.append("arm-none-eabi-gcc not found")
    if not status["dfu_util"]["path"]:
        problems.append("dfu-util not found (only needed for DFU flashing)")
    if not summary["git"]["config_submodule_initialized"]:
        problems.append("src/config submodule is empty (git submodule update --init src/config)")
    alias = status.get("space_free_alias")
    if summary["path_has_space"] and not alias:
        problems.append("checkout path contains a space - the makefiles reject it")
    data = {
        "toolchain": status,
        "problems": problems,
        "can_build": status["can_build"] and (not summary["path_has_space"] or bool(alias)),
        "space_free_alias": alias,
        "build_hint": build_core.missing_build_hint(status),
    }
    lines = [
        f"make:              {status['make']['path'] or 'MISSING'}",
        f"arm-none-eabi-gcc: {status['arm_none_eabi_gcc']['path'] or 'MISSING'}",
        f"dfu-util:          {status['dfu_util']['path'] or 'MISSING'}",
        f"git:               {status['git']['path'] or 'MISSING'}",
        f"python:            {status['python']['path']}",
        f"boards hydrated:   {'yes' if status['config_submodule'] else 'no'}",
        f"path has space:    {'yes' if status['repo_path_has_space'] else 'no'}",
        f"space-free alias:  {alias or '-'}",
        f"build via:         {status['build_via']}",
        f"can build:         {'yes' if data['can_build'] else 'no'}",
    ]
    if problems:
        lines.append("")
        lines.extend(f"! {problem}" for problem in problems)
    if data["build_hint"] and not status["can_build"]:
        lines.extend(["", data["build_hint"]])
    _emit(ctx, data, lines)


@cli.group()
def boards():
    """Inspect board definitions from src/config."""


@boards.command("list")
@click.option("--search", default=None, help="Match board, manufacturer or MCU")
@click.option("--mcu", default=None, help="Exact MCU filter, e.g. STM32F405")
@click.option("--manufacturer", default=None, help="Exact manufacturer id")
@click.pass_context
def boards_list(ctx, search, mcu, manufacturer):
    """List boards available in the checkout."""
    index = _get_index(ctx)
    if not index.available:
        raise click.ClickException(
            "src/config/configs is empty. Run: git submodule update --init src/config"
        )
    selected = index.filter(search=search, mcu=mcu, manufacturer=manufacturer)
    data = [board.as_dict() for board in selected]
    rows = [
        [b.name, b.manufacturer or "(flat)", b.mcu or "-", len(b.defines)]
        for b in selected
    ]
    lines = _table(["BOARD", "MFR", "MCU", "DEFINES"], rows)
    lines.append("")
    lines.append(f"{len(selected)} board(s)")
    _emit(ctx, data, lines)


@boards.command("show")
@click.argument("board")
@click.option("--pins/--no-pins", default=True, show_default=True,
              help="Include the pin map")
@click.pass_context
def boards_show(ctx, board, pins):
    """Show one board's identity, features and pins."""
    index = _get_index(ctx)
    selected = _get_board(index, board)
    data = selected.as_dict()
    data["defines"] = dict(sorted(selected.defines.items()))
    lines = [
        f"board:        {selected.name}",
        f"manufacturer: {selected.manufacturer or '(flat layout)'}",
        f"mcu:          {selected.mcu or 'MISSING'}",
        f"path:         {selected.path}",
        f"config.c:     {'yes' if selected.has_config_c else 'no'}",
        f"config.mk:    {'yes' if selected.has_config_mk else 'no'}",
        f"defines:      {len(selected.defines)}",
        "",
        "features:",
    ]
    lines.extend(
        ["  " + ", ".join(selected.features)] if selected.features else ["  (none)"]
    )
    if pins and selected.pins:
        lines.extend(["", "pins:"])
        rows = [[name, value] for name, value in selected.pins.items()]
        lines.extend(_table(["DEFINE", "PIN"], rows))
    _emit(ctx, data, lines)


@boards.command("diff")
@click.argument("board_a")
@click.argument("board_b")
@click.option("--category", default=None,
              help="Only show mcu|pin|feature|timer|setting changes")
@click.pass_context
def boards_diff(ctx, board_a, board_b, category):
    """Compare two board configs."""
    index = _get_index(ctx)
    data = source_core.diff_boards(
        _get_board(index, board_a), _get_board(index, board_b)
    )
    if category:
        for key in ("added", "removed", "changed"):
            data[key] = [e for e in data[key] if e["category"] == category]
        data["summary"] = {
            "added": len(data["added"]),
            "removed": len(data["removed"]),
            "changed": len(data["changed"]),
            "identical": not (data["added"] or data["removed"] or data["changed"]),
        }
    lines = [
        f"{data['a']['name']} ({data['a']['mcu']}) -> {data['b']['name']} ({data['b']['mcu']})",
        f"added {data['summary']['added']}, removed {data['summary']['removed']}, "
        f"changed {data['summary']['changed']}",
    ]
    for entry in data["added"]:
        lines.append(f"  + {entry['define']} = {entry['value']}")
    for entry in data["removed"]:
        lines.append(f"  - {entry['define']} = {entry['value']}")
    for entry in data["changed"]:
        lines.append(f"  ~ {entry['define']}: {entry['from']} -> {entry['to']}")
    if data["summary"]["identical"]:
        lines.append("  (identical)")
    _emit(ctx, data, lines)


@boards.command("validate")
@click.argument("board", required=False)
@click.option("--all", "validate_all", is_flag=True, default=False,
              help="Validate every board (same as omitting BOARD)")
@click.option("--errors-only", is_flag=True, default=False,
              help="Hide info/warning findings")
@click.pass_context
def boards_validate(ctx, board, validate_all, errors_only):
    """Lint board configs (names, manufacturer ids, MCU, duplicates)."""
    index = _get_index(ctx)
    target = None if (validate_all or not board) else _get_board(index, board)
    issues = index.validate(target)
    if errors_only:
        issues = [i for i in issues if i.severity == "error"]
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    data = {
        "board": board or "all",
        "counts": counts,
        "issues": [i.as_dict() for i in issues],
    }
    lines = [
        f"checked: {board or 'all boards'}",
        f"errors {counts['error']}, warnings {counts['warning']}, info {counts['info']}",
    ]
    for issue in issues:
        lines.append(f"  [{issue.severity}] {issue.board}: {issue.message} ({issue.code})")
    _emit(ctx, data, lines)


@boards.command("mcus")
@click.pass_context
def boards_mcus(ctx):
    """Show how many boards exist per MCU."""
    index = _get_index(ctx)
    counts = index.mcus()
    rows = [[mcu, count] for mcu, count in counts.items()]
    lines = _table(["MCU", "BOARDS"], rows)
    _emit(ctx, counts, lines)


@boards.command("manufacturers")
@click.pass_context
def boards_manufacturers(ctx):
    """Show board counts per manufacturer id."""
    index = _get_index(ctx)
    counts = index.manufacturers()
    rows = [[name, count] for name, count in counts.items()]
    lines = _table(["MANUFACTURER", "BOARDS"], rows) if counts else ["(no boards)"]
    _emit(ctx, counts, lines)


@cli.group()
def build():
    """Compile and flash firmware from source."""


@build.command("doctor")
@click.pass_context
def build_doctor(ctx):
    """Report build tool availability."""
    root = _require_repo(ctx)
    status = build_core.toolchain_status(root)
    summary = source_core.repo_summary(root)
    hint = build_core.missing_build_hint(status)
    alias = status.get("space_free_alias")
    space_ok = not summary["path_has_space"] or bool(alias)
    data = {"toolchain": status, "path_has_space": summary["path_has_space"],
            "space_free_alias": alias,
            "can_build": status["can_build"] and space_ok,
            "hint": hint}
    lines = [
        f"make:              {status['make']['path'] or 'MISSING'}",
        f"arm-none-eabi-gcc: {status['arm_none_eabi_gcc']['path'] or 'MISSING'}",
        f"dfu-util:          {status['dfu_util']['path'] or 'MISSING'}",
        f"path has space:    {'yes' if summary['path_has_space'] else 'no'}",
        f"space-free alias:  {alias or '-'}",
        f"build via:         {status['build_via']}",
        f"can build:         {'yes' if data['can_build'] else 'no'}",
    ]
    if hint:
        lines.extend(["", hint])
    _emit(ctx, data, lines)


@build.command("run")
@click.argument("board")
@click.option("-j", "--jobs", default=None, type=int, help="Parallel make jobs")
@click.option("--options", default=None, help="Betaflight OPTIONS= build flags")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print the make command without running it")
@click.pass_context
def build_run(ctx, board, jobs, options, dry_run):
    """Build firmware for BOARD (make CONFIG=BOARD)."""
    root = _require_repo(ctx)
    index = _get_index(ctx)
    selected = _get_board(index, board)
    jobs = jobs or min(os.cpu_count() or 1, 16)

    command = ["make", f"CONFIG={selected.name}", f"-j{jobs}"]
    if options:
        command.append(f"OPTIONS={options}")

    if dry_run:
        data = {"command": command, "cwd": str(root), "board": selected.name,
                "dry_run": True}
        _emit(ctx, data, [" ".join(command), f"(cwd {root})"])
        return

    try:
        result = build_core.build(
            root, selected.name, jobs=jobs, options=options,
            stream=not ctx.obj.get("json"),
        )
    except build_core.ToolchainError as exc:
        raise click.ClickException(str(exc))
    if ctx.obj.get("json"):
        _emit(ctx, result)
        return
    if not result["success"]:
        raise click.ClickException(
            f"build failed (exit {result['returncode']}); last lines:\n{result['tail']}"
        )
    arts = result["artifacts"]
    lines = [f"build ok: {selected.name} {result['version']}"]
    for note in result.get("notes", []):
        lines.append(f"note: {note}")
    for art in arts:
        lines.append(f"  {art['name']:<55} {_human_size(art['size_bytes'])}")
    _emit(ctx, result, lines)


@build.command("artifacts")
@click.argument("board")
@click.pass_context
def build_artifacts(ctx, board):
    """List built artifacts for BOARD."""
    root = _require_repo(ctx)
    items = build_core.artifacts(root, board)
    lines = [f"{item['name']:<55} {_human_size(item['size_bytes'])}" for item in items]
    if not items:
        lines = ["(no artifacts; run: build run <BOARD>)"]
    _emit(ctx, items, lines)


@build.command("clean")
@click.argument("board", required=False)
@click.option("--yes", is_flag=True, default=False, help="Confirm deletion")
@click.pass_context
def build_clean(ctx, board, yes):
    """Remove build products."""
    root = _require_repo(ctx)
    if not yes:
        raise click.ClickException("Refusing to delete build products without --yes")
    result = build_core.clean(root, board, stream=not ctx.obj.get("json"))
    _emit(ctx, result, [f"clean exit {result['returncode']}"])


@build.command("flash")
@click.argument("board")
@click.option("--method", type=click.Choice(["dfu", "serial"]), default="dfu",
              show_default=True, help="Flashing transport")
@click.option("--serial-device", default=None,
              help="Port for --method serial, e.g. COM7")
@click.option("--yes", is_flag=True, default=False, help="Confirm flashing")
@click.pass_context
def build_flash(ctx, board, method, serial_device, yes):
    """Flash firmware onto a controller (requires --yes)."""
    root = _require_repo(ctx)
    if not yes:
        raise click.ClickException(
            "Flashing rewrites the controller. Re-run with --yes to confirm."
        )
    _get_index(ctx).get(board)
    try:
        result = build_core.flash(
            root, board, method, serial_device=serial_device,
            stream=not ctx.obj.get("json"),
        )
    except build_core.ToolchainError as exc:
        raise click.ClickException(str(exc))
    _emit(ctx, result, [f"flash exit {result['returncode']}"])


@cli.group()
def device():
    """Talk to a connected flight controller over MSP."""


def _open_device(ctx) -> device_core.Device:
    try:
        return device_core.Device(
            port=ctx.obj.get("port"),
            baudrate=ctx.obj.get("baud", 115200),
            timeout=ctx.obj.get("msp_timeout", 3.0),
        )
    except Exception as exc:
        raise click.ClickException(device_core.describe_connection_error(exc))


@device.command("list")
@click.pass_context
def device_list(ctx):
    """List serial ports and mark flight controllers."""
    ports = device_core.list_ports()
    data = [p.as_dict() for p in ports]
    rows = [
        [
            p.device,
            p.description or "-",
            f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None and p.pid is not None else "-",
            "FC" if p.looks_like_fc else "",
        ]
        for p in ports
    ]
    lines = _table(["PORT", "DESCRIPTION", "VID:PID", ""], rows) if ports else ["(no serial ports)"]
    _emit(ctx, data, lines)


@device.command("info")
@click.pass_context
def device_info(ctx):
    """Read firmware, board, UID and craft name."""
    with _open_device(ctx) as dev:
        try:
            data = dev.info()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    lines = [
        f"port:      {data['port']}",
        f"variant:   {data['variant']}",
        f"firmware:  {data['firmware']}",
        f"api:       {data['api_version']['major']}.{data['api_version']['minor']} "
        f"(protocol {data['api_version']['protocol']})",
        f"board:     {data['board']}",
        f"name:      {data['craft_name']}",
        f"uid:       {data['uid']}",
        f"build:     {data['build']['date']} {data['build']['time']} "
        f"{data['build']['git']}".rstrip(),
    ]
    _emit(ctx, data, lines)


@device.command("status")
@click.pass_context
def device_status(ctx):
    """Read voltage, attitude, motors, RC channels and active modes."""
    with _open_device(ctx) as dev:
        try:
            data = dev.status()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    analog = data.get("analog") or {}
    attitudes = data.get("attitude") or {}
    lines = [
        f"port:      {data['port']}",
        f"voltage:   {analog.get('voltage_v', '?')} V",
        f"current:   {analog.get('amperage_a', '?')} A",
        f"attitude:  roll {attitudes.get('roll_deg', '?')} / "
        f"pitch {attitudes.get('pitch_deg', '?')} / yaw {attitudes.get('yaw_deg', '?')}",
        f"motors:    {data.get('motors')}",
        f"rc:        {data.get('rc_channels')}",
        f"modes:     {', '.join(data.get('modes') or []) or '(none active)'}",
    ]
    battery = data.get("battery")
    if battery:
        lines.append(
            f"battery:   {battery.get('cells')}S "
            f"{battery.get('voltage_v')} V "
            f"({battery.get('voltage_per_cell_v')} V/cell), "
            f"{battery.get('mah_drawn')} mAh used"
        )
    _emit(ctx, data, lines)


@device.group("config")
def device_config():
    """Read and write firmware settings."""


@device_config.command("get")
@click.argument("name")
@click.pass_context
def device_config_get(ctx, name):
    """Read one setting by name."""
    with _open_device(ctx) as dev:
        try:
            data = dev.config_get(name)
        except (msp.MspError, device_core.DeviceError) as exc:
            raise click.ClickException(str(exc))
    _emit(ctx, data, [f"{data['name']} = {data['value']}"])


@device_config.command("set")
@click.argument("name")
@click.argument("value")
@click.option("--yes", is_flag=True, default=False,
              help="Confirm writing to RAM")
@click.pass_context
def device_config_set(ctx, name, value, yes):
    """Write a setting to RAM (does not persist until 'device save')."""
    if not yes:
        raise click.ClickException(
            f"Would set {name} = {value} in RAM. Re-run with --yes to apply."
        )
    with _open_device(ctx) as dev:
        try:
            data = dev.config_set(name, value)
        except (msp.MspError, device_core.DeviceError) as exc:
            raise click.ClickException(str(exc))
    _emit(
        ctx,
        data,
        [
            f"{data['name']} = {data['value']} (RAM only)",
            "Persist with: cli-anything-betaflight device save --yes",
        ],
    )


@device_config.command("diff")
@click.option("--all", "all_profiles", is_flag=True, default=False,
              help="Include every profile ('diff all')")
@click.pass_context
def device_config_diff(ctx, all_profiles):
    """Show settings that differ from the firmware defaults."""
    with _open_device(ctx) as dev:
        try:
            text = dev.config_diff(all_profiles)
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    if ctx.obj.get("json"):
        _emit(ctx, {"diff_all": all_profiles, "text": text})
    else:
        click.echo(text.rstrip() or "(empty diff)")


@device_config.command("dump")
@click.pass_context
def device_config_dump(ctx):
    """Dump the full firmware configuration."""
    with _open_device(ctx) as dev:
        try:
            text = dev.config_dump()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    if ctx.obj.get("json"):
        _emit(ctx, {"text": text})
    else:
        click.echo(text.rstrip())


@device.command("settings")
@click.option("--prefix", default="", help="Only settings whose name contains this")
@click.pass_context
def device_settings(ctx, prefix):
    """List firmware settings (like the GUI's settings panels)."""
    with _open_device(ctx) as dev:
        try:
            items = dev.settings(prefix)
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    if ctx.obj.get("json"):
        _emit(ctx, items)
        return
    for item in items:
        click.echo(f"{item['name']} = {item['value']}")
    click.echo(f"\n{len(items)} setting(s)")


@device.command("features")
@click.option("--enable", default=None, metavar="NAME", help="Enable a feature")
@click.option("--disable", default=None, metavar="NAME", help="Disable a feature")
@click.option("--yes", is_flag=True, default=False, help="Confirm the change")
@click.pass_context
def device_features(ctx, enable, disable, yes):
    """List features or toggle one (like the GUI's Configuration tab)."""
    if enable and disable:
        raise click.ClickException("Use either --enable or --disable, not both")
    write = enable or disable
    if write and not yes:
        raise click.ClickException(
            f"Would {'enable' if enable else 'disable'} feature "
            f"'{write}' (RAM only). Re-run with --yes."
        )
    with _open_device(ctx) as dev:
        try:
            if write:
                text = dev.feature_set(write, enabled=bool(enable))
                data = {"feature": write, "enabled": bool(enable), "output": text}
                _emit(ctx, data, [f"{'enabled' if enable else 'disabled'} {write} (RAM only)",
                                   "Persist with: device save --yes"])
                return
            data = dev.features()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    lines = []
    for key in ("enabled", "available", "unavailable"):
        lines.append(f"{key}: {', '.join(data[key]) or '(none)'}")
    _emit(ctx, data, lines)


@device.command("ports")
@click.pass_context
def device_ports(ctx):
    """Show UART/USB port assignments (like the GUI's Ports tab)."""
    with _open_device(ctx) as dev:
        try:
            data = dev.ports()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    _emit(ctx, data, data["raw"])


@device.command("modes")
@click.pass_context
def device_modes(ctx):
    """Show AUX mode ranges (like the GUI's Modes tab)."""
    with _open_device(ctx) as dev:
        try:
            data = dev.modes()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    rows = [
        [m["index"], m["mode_id"], m["aux_channel"], m["start"], m["end"], m["logic"]]
        for m in data
    ]
    lines = _table(
        ["INDEX", "MODE", "AUX", "START", "END", "LOGIC"], rows
    ) if rows else ["(no mode ranges)"]
    _emit(ctx, data, lines)


@device.command("backup")
@click.argument("path", type=click.Path(dir_okay=False))
@click.pass_context
def device_backup(ctx, path):
    """Save a full CLI backup (dump + metadata) to a file."""
    target = Path(path).expanduser()
    with _open_device(ctx) as dev:
        try:
            text = dev.backup_text()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    data = {"path": str(target), "bytes": len(text.encode("utf-8"))}
    _emit(ctx, data, [f"backup written: {target} ({data['bytes']} bytes)"])


@device.command("restore")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", is_flag=True, default=False,
              help="Confirm overwriting the controller's settings")
@click.option("--save", is_flag=True, default=False,
              help="Persist after restoring (reboots the controller)")
@click.pass_context
def device_restore(ctx, path, yes, save):
    """Replay a CLI backup (requires --yes; nothing is saved unless --save)."""
    if not yes:
        raise click.ClickException(
            "Restoring overwrites the controller's settings. Re-run with --yes."
        )
    text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    with _open_device(ctx) as dev:
        try:
            result = dev.restore(text)
            if save and not result["errors"]:
                dev.save()
                result["saved"] = True
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    lines = [
        f"executed {result['executed']} command(s)",
        f"errors: {len(result['errors'])}",
    ]
    for error in result["errors"][:10]:
        lines.append(f"  ! {error['command']} -> {error['error'][:120]}")
    if result.get("saved"):
        lines.append("saved to flash; controller reboots")
    _emit(ctx, result, lines)


@device.command("motor")
@click.option("--motor", type=int, required=True, help="Motor number (1-8)")
@click.option("--throttle", type=int, default=1050, show_default=True,
              help="Throttle value 1000-2000")
@click.option("--seconds", type=float, default=2.0, show_default=True,
              help="How long to spin (max 10s)")
@click.option("--yes", is_flag=True, default=False,
              help="Confirm props are removed")
@click.pass_context
def device_motor(ctx, motor, throttle, seconds, yes):
    """Spin one motor briefly (PROPS OFF; requires --yes)."""
    if not yes:
        raise click.ClickException(
            "Motor test spins a motor. REMOVE ALL PROPS and re-run with --yes."
        )
    if seconds > 10:
        raise click.ClickException("--seconds is limited to 10 for safety")
    with _open_device(ctx) as dev:
        try:
            data = dev.motor_test(motor, throttle, seconds)
        except (msp.MspError, device_core.DeviceError) as exc:
            raise click.ClickException(str(exc))
    _emit(
        ctx,
        data,
        [f"motor {motor} at {throttle} for {seconds}s - stopped"],
    )


@device.command("acc-calibrate")
@click.option("--yes", is_flag=True, default=False, help="Confirm calibration")
@click.pass_context
def device_acc_calibrate(ctx, yes):
    """Calibrate the accelerometer (craft must be level and still)."""
    if not yes:
        raise click.ClickException(
            "Calibration must start with the craft level. Re-run with --yes."
        )
    with _open_device(ctx) as dev:
        try:
            data = dev.acc_calibrate()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    _emit(ctx, data, ["accelerometer calibration started"])


@device.command("flash")
@click.argument("firmware", type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", is_flag=True, default=False, help="Confirm flashing")
@click.pass_context
def device_flash(ctx, firmware, yes):
    """Flash a .hex/.bin over DFU (controller must be in DFU mode)."""
    if not yes:
        raise click.ClickException(
            "Flashing rewrites the firmware. Re-run with --yes."
        )
    try:
        data = device_core.dfu_flash(Path(firmware))
    except device_core.DeviceError as exc:
        raise click.ClickException(str(exc))
    lines = [f"dfu-util exit {data['returncode']}"]
    if not data["success"]:
        lines.append(data["output"].strip()[-1500:])
    _emit(ctx, data, lines)


@device.command("dfu-list")
@click.pass_context
def device_dfu_list(ctx):
    """List controllers currently in DFU mode."""
    try:
        data = device_core.dfu_list_devices()
    except device_core.DeviceError as exc:
        raise click.ClickException(str(exc))
    _emit(ctx, data, [data["output"].strip() or "(no DFU device)"])


@device.command("cli")
@click.argument("command", nargs=-1, required=True)
@click.option("--force", is_flag=True, default=False,
              help="Allow commands that change the controller")
@click.pass_context
def device_cli(ctx, command, force):
    """Run a firmware CLI command, e.g. 'device cli status'."""
    command_line = " ".join(command).strip()
    try:
        device_core.guard_command(command_line, force)
    except device_core.DeviceError as exc:
        raise click.ClickException(str(exc))
    with _open_device(ctx) as dev:
        try:
            text = dev.cli(command_line)
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    if ctx.obj.get("json"):
        _emit(ctx, {"command": command_line, "text": text})
    else:
        click.echo(text.rstrip())


@device.command("save")
@click.option("--yes", is_flag=True, default=False, help="Confirm writing to flash")
@click.pass_context
def device_save(ctx, yes):
    """Persist settings to flash (the controller reboots)."""
    if not yes:
        raise click.ClickException(
            "Saving rewrites the controller's flash. Re-run with --yes to confirm."
        )
    with _open_device(ctx) as dev:
        try:
            data = dev.save()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    _emit(ctx, data, ["saved; the controller reboots"])


@device.command("reboot")
@click.option("--yes", is_flag=True, default=False, help="Confirm reboot")
@click.pass_context
def device_reboot(ctx, yes):
    """Reboot the flight controller."""
    if not yes:
        raise click.ClickException("Re-run with --yes to reboot the controller.")
    with _open_device(ctx) as dev:
        try:
            data = dev.reboot()
        except msp.MspError as exc:
            raise click.ClickException(str(exc))
    _emit(ctx, data, ["reboot requested"])


@cli.command("repl")
@click.pass_context
def repl(ctx):
    """Interactive shell (default when no command is given)."""
    skin = ReplSkin("betaflight", version=VERSION)
    try:
        skin.print_banner()
    except UnicodeEncodeError:
        click.echo(f"cli-anything-betaflight {VERSION}")
    root = ctx.obj.get("repo")
    if root:
        skin.info(f"repo: {root}")
    else:
        skin.warning(ctx.obj.get("repo_error") or "no repo")

    base: list[str] = []
    if root:
        base += ["--repo", str(root)]
    if ctx.obj.get("port"):
        base += ["--port", ctx.obj["port"]]
    base += ["--baud", str(ctx.obj.get("baud", 115200))]

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    session = skin.create_prompt_session() if interactive else None
    while True:
        try:
            if session is not None:
                line = skin.get_input(session, project_name="")
            else:
                line = input("betaflight> ")
        except (EOFError, KeyboardInterrupt):
            break
        line = (line or "").strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered in {"exit", "quit", "q"}:
            break
        if lowered in {"help", "?"}:
            skin.help(COMMANDS)
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            skin.error(str(exc))
            continue
        try:
            cli.main(args=base + args, prog_name="cli-anything-betaflight",
                     standalone_mode=False)
        except click.ClickException as exc:
            skin.error(exc.format_message())
        except SystemExit:
            pass
        except Exception as exc:
            skin.error(str(exc))
    skin.print_goodbye()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    try:
        cli.main(args=argv, prog_name="cli-anything-betaflight",
                 standalone_mode=False)
        return 0
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except (source_core.RepoError, build_core.ToolchainError,
            device_core.DeviceError, msp.MspError) as exc:
        click.echo(f"error: {exc}", err=True)
        return 1
    except KeyboardInterrupt:
        click.echo("\naborted", err=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
