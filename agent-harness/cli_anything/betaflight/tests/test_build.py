from __future__ import annotations

from pathlib import Path

from cli_anything.betaflight.core import build


def test_to_msys_path_converts_drive_letter():
    assert build.to_msys_path(Path(r"C:\betaflight")) == "/c/betaflight"
    assert (
        build.to_msys_path(Path(r"C:\Users\example\My Projects\betaflight"))
        == "/c/Users/example/My Projects/betaflight"
    )


def test_to_msys_path_leaves_posix_paths_alone():
    assert build.to_msys_path(Path("/home/larp/betaflight")) == "/home/larp/betaflight"


def test_toolchain_status_shape():
    status = build.toolchain_status()
    for key in ("make", "arm_none_eabi_gcc", "dfu_util", "msys2", "build_via"):
        assert key in status
    assert isinstance(status["can_build"], bool)
    assert status["build_via"] in {"msys2", "native"}


def test_toolchain_status_flags_space_and_alias(tmp_path):
    repo = tmp_path / "with space" / "betaflight"
    repo.mkdir(parents=True)
    status = build.toolchain_status(repo)
    assert status["repo_path_has_space"] is True
    assert status["space_free_alias"] is None
