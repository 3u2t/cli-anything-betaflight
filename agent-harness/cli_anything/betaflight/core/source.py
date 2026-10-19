"""Read a Betaflight source checkout: repo state, boards, validation, diffs.

Betaflight keeps board defaults in the ``src/config`` submodule:

    src/config/configs/<MANUFACTURER_ID>/<BOARD>/config.h

Older trees also use a flat ``configs/<BOARD>/config.h`` layout; both are read.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MCU_PATTERN = re.compile(r"^(STM32|AT32|APM32|GD32|RP|PICO|MSP|SITL)[A-Z0-9_]*$")
DEFINE_RE = re.compile(
    r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)(?!\()\s*(.*?)\s*$"
)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"//.*$", re.M)

REQUIRED_DEFINES = ("FC_TARGET_MCU", "BOARD_NAME", "MANUFACTURER_ID")

# Used when a checkout has no boards hydrated yet.
KNOWN_MCUS = {
    "STM32F405", "STM32F411", "STM32F446", "STM32F722", "STM32F745",
    "STM32F765", "STM32G47X", "STM32H723", "STM32H725", "STM32H730",
    "STM32H743", "STM32H750", "STM32H735", "AT32F435", "AT32F437",
    "APM32F405", "APM32F407", "APM32F411", "GD32F405", "GD32F407",
    "STM32N657", "STM32C562",
}


class RepoError(RuntimeError):
    """Raised when a Betaflight checkout cannot be used."""


@dataclass
class Issue:
    severity: str
    board: str
    code: str
    message: str

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "board": self.board,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class Board:
    name: str
    manufacturer: str | None
    mcu: str | None
    defines: dict[str, str]
    path: Path
    has_config_c: bool = False
    has_config_mk: bool = False

    @property
    def features(self) -> list[str]:
        return sorted(k[4:] for k in self.defines if k.startswith("USE_"))

    @property
    def pins(self) -> dict[str, str]:
        return {
            k: v
            for k, v in sorted(self.defines.items())
            if k.endswith("_PIN") and v
        }

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "manufacturer": self.manufacturer,
            "mcu": self.mcu,
            "path": str(self.path),
            "has_config_c": self.has_config_c,
            "has_config_mk": self.has_config_mk,
            "define_count": len(self.defines),
            "features": self.features,
            "pins": self.pins,
        }


def _clean_source(text: str) -> str:
    text = BLOCK_COMMENT_RE.sub("", text)
    return LINE_COMMENT_RE.sub("", text)


def parse_defines(text: str) -> dict[str, str]:
    """Parse object-like #defines, ignoring function-like macros."""
    defines: dict[str, str] = {}
    for line in _clean_source(text).splitlines():
        match = DEFINE_RE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        defines[name] = value
    return defines


def find_repo(start: Path | str | None = None) -> Path:
    """Locate a Betaflight checkout (contains Makefile + src/main/cli)."""
    env = os.environ.get("BETAFLIGHT_REPO")
    candidates: list[Path] = []
    if start:
        candidates.append(Path(start))
    if env:
        candidates.append(Path(env))
    here = Path.cwd()
    search_roots = [here, *here.parents]
    candidates.extend(search_roots)
    for root in search_roots:
        candidates.append(root / "betaflight")
        candidates.append(root / "upstream" / "betaflight")
    for candidate in candidates:
        try:
            # abspath, not resolve(): keep junctions like C:\betaflight intact.
            candidate = Path(os.path.abspath(candidate.expanduser()))
        except OSError:
            continue
        if (candidate / "Makefile").is_file() and (
            candidate / "src" / "main" / "cli"
        ).is_dir():
            return candidate
    raise RepoError(
        "No Betaflight checkout found. Pass --repo PATH or set BETAFLIGHT_REPO."
    )


def git_info(repo: Path) -> dict:
    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    status = run("status", "--porcelain")
    submodules = run("submodule", "status")
    return {
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": run("rev-parse", "--short=7", "HEAD"),
        "describe": run("describe", "--tags", "--always"),
        "dirty_files": len([line for line in status.splitlines() if line.strip()]),
        "submodules": [
            line.strip() for line in submodules.splitlines() if line.strip()
        ],
        "config_submodule_initialized": (repo / "src" / "config" / "configs").is_dir(),
    }


class BoardIndex:
    """Lazily parsed index of every board config in a checkout."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.config_dir = repo / "src" / "config" / "configs"
        self._boards: dict[str, Board] | None = None
        self._by_name: dict[str, list[Board]] | None = None

    @property
    def available(self) -> bool:
        return self.config_dir.is_dir()

    def _iter_headers(self):
        if not self.available:
            return
        yield from sorted(self.config_dir.glob("*/config.h"))
        yield from sorted(self.config_dir.glob("*/*/config.h"))

    def load(self) -> dict[str, Board]:
        if self._boards is not None:
            return self._boards
        boards: dict[str, Board] = {}
        by_name: dict[str, list[Board]] = {}
        for header in self._iter_headers():
            board_dir = header.parent
            name = board_dir.name
            manufacturer = None
            if board_dir.parent != self.config_dir:
                manufacturer = board_dir.parent.name
            defines = parse_defines(header.read_text(encoding="utf-8", errors="replace"))
            board = Board(
                name=name,
                manufacturer=manufacturer,
                mcu=defines.get("FC_TARGET_MCU"),
                defines=defines,
                path=board_dir,
                has_config_c=(board_dir / "config.c").is_file(),
                has_config_mk=(board_dir / "config.mk").is_file(),
            )
            boards[str(board_dir)] = board
            by_name.setdefault(name.upper(), []).append(board)
        self._boards = boards
        self._by_name = by_name
        return boards

    def all(self) -> list[Board]:
        return sorted(self.load().values(), key=lambda b: b.name.upper())

    def get(self, name: str) -> Board:
        self.load()
        matches = self._by_name.get(name.upper(), [])  # type: ignore[union-attr]
        if not matches:
            raise RepoError(f"Unknown board '{name}'")
        if len(matches) > 1:
            locations = ", ".join(f"{m.manufacturer or 'flat'}/{m.name}" for m in matches)
            raise RepoError(f"Board name '{name}' is ambiguous: {locations}")
        return matches[0]

    def filter(
        self,
        search: str | None = None,
        mcu: str | None = None,
        manufacturer: str | None = None,
    ) -> list[Board]:
        boards = self.all()
        if search:
            needle = search.lower()
            boards = [
                b
                for b in boards
                if needle in b.name.lower()
                or needle in (b.manufacturer or "").lower()
                or needle in (b.mcu or "").lower()
            ]
        if mcu:
            wanted = mcu.upper()
            boards = [b for b in boards if (b.mcu or "").upper() == wanted]
        if manufacturer:
            wanted_mfr = manufacturer.upper()
            boards = [
                b for b in boards if (b.manufacturer or "").upper() == wanted_mfr
            ]
        return boards

    def manufacturers(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for board in self.all():
            key = board.manufacturer or "(flat)"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def mcus(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for board in self.all():
            key = board.mcu or "(none)"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    # -- validation --------------------------------------------------------

    def validate(self, board: Board | None = None) -> list[Issue]:
        boards = [board] if board else self.all()
        issues: list[Issue] = []
        seen: dict[str, list[Board]] = {}
        for b in self.all():
            seen.setdefault(b.name.upper(), []).append(b)

        known_mcus = {b.mcu for b in self.all() if b.mcu} | KNOWN_MCUS

        for b in boards:
            label = f"{b.manufacturer}/{b.name}" if b.manufacturer else b.name
            if len(seen.get(b.name.upper(), [])) > 1:
                others = ", ".join(
                    f"{o.manufacturer or 'flat'}/{o.name}"
                    for o in seen[b.name.upper()]
                    if o is not b
                )
                issues.append(
                    Issue("error", label, "duplicate-name",
                          f"board name also used by {others}")
                )
            for define in REQUIRED_DEFINES:
                if not b.defines.get(define):
                    issues.append(
                        Issue("error", label, f"missing-{define.lower()}",
                              f"{define} is not defined")
                    )
            board_name = b.defines.get("BOARD_NAME")
            if board_name and board_name != b.name:
                issues.append(
                    Issue("error", label, "board-name-mismatch",
                          f"BOARD_NAME is '{board_name}' but the directory is '{b.name}'")
                )
            mfr = b.defines.get("MANUFACTURER_ID")
            if b.manufacturer and mfr and mfr != b.manufacturer:
                issues.append(
                    Issue("error", label, "manufacturer-mismatch",
                          f"MANUFACTURER_ID is '{mfr}' but the directory is "
                          f"'{b.manufacturer}'")
                )
            if not b.manufacturer:
                issues.append(
                    Issue("warning", label, "flat-layout",
                          "config lives directly under configs/ instead of "
                          "configs/<MANUFACTURER_ID>/<BOARD>/")
                )
            mcu = b.defines.get("FC_TARGET_MCU")
            if mcu:
                if not MCU_PATTERN.match(mcu):
                    issues.append(
                        Issue("error", label, "mcu-format",
                              f"FC_TARGET_MCU '{mcu}' does not look like an MCU")
                    )
                elif mcu not in known_mcus:
                    issues.append(
                        Issue("warning", label, "mcu-unknown",
                              f"MCU '{mcu}' is not used by any other board")
                    )
            if not b.defines.get("TIMER_PIN_MAPPING"):
                issues.append(
                    Issue("warning", label, "no-timer-mapping",
                          "TIMER_PIN_MAPPING is missing (fine for some MCUs)")
                )
            if not b.has_config_mk:
                issues.append(
                    Issue("info", label, "no-config-mk",
                          "no config.mk (only needed for special build options)")
                )
        return issues


def diff_boards(a: Board, b: Board) -> dict:
    """Compare two board configs."""
    keys = sorted(set(a.defines) | set(b.defines))
    added, removed, changed = [], [], []
    for key in keys:
        left, right = a.defines.get(key), b.defines.get(key)
        if left is None:
            added.append({"define": key, "value": right})
        elif right is None:
            removed.append({"define": key, "value": left})
        elif left != right:
            changed.append({"define": key, "from": left, "to": right})

    def category(define: str) -> str:
        if define == "FC_TARGET_MCU":
            return "mcu"
        if define.endswith("_PIN"):
            return "pin"
        if define.startswith("USE_"):
            return "feature"
        if define.startswith("TIMER") or define.startswith("DMA"):
            return "timer"
        return "setting"

    for entry in added + removed + changed:
        entry["category"] = category(entry["define"])
    return {
        "a": {"name": a.name, "manufacturer": a.manufacturer, "mcu": a.mcu},
        "b": {"name": b.name, "manufacturer": b.manufacturer, "mcu": b.mcu},
        "added": added,
        "removed": removed,
        "changed": changed,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "identical": not (added or removed or changed),
        },
    }


def repo_summary(repo: Path) -> dict:
    index = BoardIndex(repo)
    boards = index.all() if index.available else []
    return {
        "root": str(repo),
        "path_has_space": " " in str(repo),
        "boards_available": index.available,
        "board_count": len(boards),
        "manufacturer_count": len({b.manufacturer for b in boards if b.manufacturer}),
        "mcu_count": len({b.mcu for b in boards if b.mcu}),
        "git": git_info(repo),
    }
