"""Toolchain detection and build/flash orchestration for a Betaflight checkout."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

MAKE_NAMES = ("make", "gmake", "mingw32-make")
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MSYS2_CANDIDATES = (
    r"C:\msys64",
    r"C:\msys2",
    r"C:\tools\msys64",
)


class ToolchainError(RuntimeError):
    """Raised when a build or flash cannot start."""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0].strip() if text else None


def find_msys2() -> Path | None:
    """Locate an MSYS2 installation (bash.exe is the reliable marker)."""
    candidates = []
    override = os.environ.get("BETAFLIGHT_MSYS2")
    if override:
        candidates.append(override)
    candidates.extend(MSYS2_CANDIDATES)
    for raw in candidates:
        root = Path(raw)
        if (root / "usr" / "bin" / "bash.exe").is_file():
            return root
    return None


def to_msys_path(path: Path) -> str:
    """C:\\betaflight -> /c/betaflight (no resolve(): keep junctions intact)."""
    text = str(path).replace("\\", "/")
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    if match:
        return f"/{match.group(1).lower()}/{match.group(2)}"
    return text


def find_arm_gcc() -> str | None:
    found = _which("arm-none-eabi-gcc")
    if found:
        return found
    roots = [
        Path.home() / "Documents" / "tools",
        Path(r"C:\msys64\ucrt64\bin"),
        Path(r"C:\msys64\mingw64\bin"),
    ]
    patterns = (
        "arm-gnu-toolchain-*/bin/arm-none-eabi-gcc.exe",
        "arm-none-eabi*/bin/arm-none-eabi-gcc.exe",
        "arm-none-eabi-gcc.exe",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            for match in sorted(root.glob(pattern)):
                if match.is_file():
                    return str(match)
    return None


def find_dfu() -> str | None:
    found = _which("dfu-util")
    if found:
        return found
    root = Path(r"C:\Tools")
    for pattern in ("dfu-util-*/win64/dfu-util.exe", "dfu-util*/dfu-util.exe"):
        for match in sorted(root.glob(pattern)):
            if match.is_file():
                return str(match)
    return None


def find_make() -> str | None:
    for name in MAKE_NAMES:
        path = _which(name)
        if path:
            return path
    msys = find_msys2()
    if msys:
        make = msys / "usr" / "bin" / "make.exe"
        if make.is_file():
            return str(make)
    return None


def space_free_alias(repo: Path) -> Path | None:
    """A junction/symlink to repo whose path has no spaces, if one exists."""
    try:
        target = repo.resolve()
    except OSError:
        return None
    for candidate in (Path(r"C:\betaflight"), Path(r"C:\bf"), Path.home() / "betaflight"):
        if " " in str(candidate) or not candidate.is_dir():
            continue
        try:
            if candidate.resolve() == target:
                return candidate
        except OSError:
            continue
    return None


def toolchain_status(repo: Path | None = None) -> dict:
    make = find_make()
    gcc = find_arm_gcc()
    dfu = find_dfu()
    openocd = _which("openocd")
    msys2 = find_msys2()
    data = {
        "make": {"path": make, "version": _version([make, "--version"]) if make else None},
        "arm_none_eabi_gcc": {
            "path": gcc,
            "version": _version([gcc, "-dumpversion"]) if gcc else None,
        },
        "dfu_util": {"path": dfu, "version": _version([dfu, "--version"]) if dfu else None},
        "openocd": {
            "path": openocd,
            "version": _version([openocd, "--version"]) if openocd else None,
        },
        "python": {"path": sys.executable, "version": sys.version.split()[0]},
        "git": {"path": _which("git"),
                "version": _version(["git", "--version"]) if _which("git") else None},
        "msys2": {"root": str(msys2) if msys2 else None,
                  "bash": str(msys2 / "usr" / "bin" / "bash.exe") if msys2 else None},
        "build_via": "msys2" if (os.name == "nt" and msys2) else "native",
    }
    data["can_build"] = bool(make and gcc)
    if repo is not None:
        data["repo"] = str(repo)
        data["repo_path_has_space"] = " " in str(repo)
        data["config_submodule"] = (repo / "src" / "config" / "configs").is_dir()
        alias = space_free_alias(repo) if data["repo_path_has_space"] else None
        data["space_free_alias"] = str(alias) if alias else None
    return data


def missing_build_hint(status: dict) -> str:
    missing = [name for name in ("make", "arm_none_eabi_gcc") if not status[name]["path"]]
    if not missing:
        return ""
    hints = [
        f"Missing build tools: {', '.join(n.replace('_', '-') for n in missing)}.",
        "Betaflight builds with GNU make and ARM GNU GCC 13.3.1.",
        "",
        "Windows (MSYS2, already works on this machine):",
        "  winget install MSYS2.MSYS2",
        "  # in the MSYS2 MINGW64 shell:",
        "  pacman -S make git unzip curl",
        "  make arm_sdk_install",
        "",
        "WSL (Ubuntu):",
        "  sudo apt install build-essential git gcc-arm-none-eabi",
        "  # clone the repo inside the Linux filesystem (space-free path!)",
    ]
    return "\n".join(hints)


def firmware_version(repo: Path) -> str:
    version_h = repo / "src" / "main" / "build" / "version.h"
    try:
        text = version_h.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    values = {}
    for key in ("YEAR", "MONTH", "PATCH_LEVEL"):
        match = re.search(rf"#define\s+FC_VERSION_{key}\s+(\d+)", text)
        if match:
            values[key] = match.group(1)
    if len(values) != 3:
        return "unknown"
    suffix = ""
    match = re.search(r'#define\s+FC_VERSION_SUFFIX\s+"([^"]*)"', text)
    if match and match.group(1):
        suffix = "-" + match.group(1)
    return f"{values['YEAR']}.{values['MONTH']}.{values['PATCH_LEVEL']}{suffix}"


def run_make(
    repo: Path,
    args: list[str],
    stream: bool = True,
    env: dict | None = None,
) -> dict:
    make = find_make()
    if not make:
        raise ToolchainError(missing_build_hint(toolchain_status(repo)))

    process_env = dict(os.environ)
    process_env.setdefault("PYTHON", sys.executable)
    if env:
        process_env.update(env)

    dfu = find_dfu()
    if dfu:
        process_env["PATH"] = str(Path(dfu).parent) + os.pathsep + process_env.get("PATH", "")

    msys = find_msys2() if os.name == "nt" else None
    use_msys = bool(msys and (repo / "Makefile").is_file())
    cwd: str | None = str(repo)

    if use_msys:
        script = "cd {} && make {}".format(
            shlex.quote(to_msys_path(repo)),
            " ".join(shlex.quote(str(arg)) for arg in args),
        )
        command = [str(msys / "usr" / "bin" / "bash.exe"), "-lc", script]
        process_env.setdefault("MSYSTEM", "MINGW64")
        cwd = None
    else:
        command = [make, *args]

    if stream:
        collected: list[str] = []
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=process_env,
            creationflags=CREATE_NO_WINDOW,
        )
        assert process.stdout is not None
        for line in process.stdout:
            collected.append(line)
            click_echo(line.rstrip("\r\n"))
        returncode = process.wait()
        output = "".join(collected)
    else:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=process_env,
            creationflags=CREATE_NO_WINDOW,
        )
        returncode = result.returncode
        output = (result.stdout or "") + (result.stderr or "")

    return {
        "command": command,
        "cwd": str(repo),
        "returncode": returncode,
        "success": returncode == 0,
        "output": output,
        "tail": "\n".join(output.strip().splitlines()[-25:]),
    }


def click_echo(text: str) -> None:
    import click

    click.echo(text)


def build(
    repo: Path,
    board: str,
    jobs: int | None = None,
    options: str | None = None,
    stream: bool = True,
    extra: list[str] | None = None,
) -> dict:
    repo = Path(repo)
    notes: list[str] = []
    if " " in str(repo):
        alias = space_free_alias(repo)
        if alias:
            notes.append(f"building through space-free alias {alias}")
            repo = alias
        else:
            raise ToolchainError(
                "This checkout path contains a space:\n"
                f"  {repo}\n"
                "Betaflight's makefiles reject whitespace in CONFIG_DIR. Create a\n"
                "space-free junction first:\n"
                "  cmd /c mklink /J C:\\betaflight \"<this path>\"\n"
                "and pass --repo C:\\betaflight."
            )
    status = toolchain_status(repo)
    if not status["can_build"]:
        raise ToolchainError(missing_build_hint(status))

    args = [f"CONFIG={board}"]
    if jobs:
        args.append(f"-j{int(jobs)}")
    if options:
        args.append(f"OPTIONS={options}")
    if extra:
        args.extend(extra)
    result = run_make(repo, args, stream=stream)
    result["board"] = board
    result["version"] = firmware_version(repo)
    result["artifacts"] = artifacts(repo, board)
    result["repo"] = str(repo)
    result["notes"] = notes
    return result


def clean(repo: Path, board: str | None = None, stream: bool = True) -> dict:
    repo = Path(repo)
    args = ["clean"]
    if board:
        args = [f"CONFIG={board}", "clean"]
    return run_make(repo, args, stream=stream)


def artifacts(repo: Path, board: str) -> list[dict]:
    obj = repo / "obj"
    if not obj.is_dir():
        return []
    found: list[Path] = []
    for pattern in (f"*{board}*.hex", f"*{board}*.bin", f"*{board}*.elf",
                    f"*{board}*.dfu", f"*{board}*.zip"):
        found.extend(obj.glob(pattern))
        found.extend((obj / "main").glob(pattern))
    unique = {str(p.resolve()): p for p in found if p.is_file()}
    items = []
    for path in sorted(unique.values(), key=lambda p: p.name):
        stat = path.stat()
        items.append(
            {
                "path": str(path),
                "name": path.name,
                "suffix": path.suffix,
                "size_bytes": stat.st_size,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": int(stat.st_mtime),
            }
        )
    return items


FLASH_METHODS = {
    "dfu": "dfu_flash",
    "serial": "tty_flash",
}


def flash(
    repo: Path,
    board: str,
    method: str = "dfu",
    serial_device: str | None = None,
    stream: bool = True,
) -> dict:
    repo = Path(repo)
    if method not in FLASH_METHODS:
        raise ToolchainError(
            f"Unknown flash method '{method}'. Use one of: {', '.join(FLASH_METHODS)}"
        )
    if method == "dfu" and not find_dfu():
        raise ToolchainError(
            "dfu-util is not installed. Install it, or put the flight controller\n"
            "in DFU mode and use Betaflight Configurator."
        )
    if method == "serial" and not shutil.which("stm32flash"):
        raise ToolchainError(
            "stm32flash is not installed. Install it, or use --method dfu."
        )
    if " " in str(repo):
        alias = space_free_alias(repo)
        if alias:
            repo = alias
        else:
            raise ToolchainError(
                "This checkout path contains a space; create C:\\betaflight with\n"
                "  cmd /c mklink /J C:\\betaflight \"<this path>\""
            )
    args = [f"CONFIG={board}", FLASH_METHODS[method]]
    if serial_device:
        args.append(f"SERIAL_DEVICE={serial_device}")
    result = run_make(repo, args, stream=stream)
    result["method"] = method
    result["board"] = board
    result["repo"] = str(repo)
    return result


def artifacts_as_json(items: list[dict]) -> str:
    return json.dumps(items, indent=2)
