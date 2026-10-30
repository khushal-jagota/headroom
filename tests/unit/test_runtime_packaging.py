"""Relocation gaps: wheel-private resolution and generated console scripts."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from planner.environments.runtime_contracts import RuntimeContractError
from planner.environments.runtime_packaging import (
    _copied_component,
    _inactive_musl,
    _native_role,
    _provider,
    _relative_paths,
    _rewrite_console_scripts,
)


def test_wheel_private_library_wins_after_archive_relocation(tmp_path: Path) -> None:
    original = tmp_path / "builder"
    extension = original / "site-packages/wheel/native.so"
    extension.parent.mkdir(parents=True)
    extension.touch()
    private = original / "site-packages/wheel.libs"
    private.mkdir()
    (private / "libshared.so").write_text("wheel ABI")
    shared = original / "lib"
    shared.mkdir()
    (shared / "libshared.so").write_text("different ABI")
    paths = _relative_paths(["$ORIGIN/../wheel.libs", "/builder/lib"], extension, shared)
    relocated = tmp_path / "unrelated directory"
    shutil.move(original, relocated)
    provider = _provider("libshared.so", relocated / extension.relative_to(original), paths, {})
    assert provider.read_text() == "wheel ABI"
    with pytest.raises(RuntimeContractError, match="unresolved native dependency"):
        _provider("missing.so", relocated / extension.relative_to(original), paths, {})


def test_console_script_runs_after_prefix_moves(tmp_path: Path) -> None:
    prefix = tmp_path / "build-prefix"
    binaries = prefix / "bin"
    binaries.mkdir(parents=True)
    (binaries / "python").symlink_to(sys.executable)
    script = binaries / "example"
    script.write_text(f"#!{sys.executable}\nimport sys; print(sys.argv[1])\n")
    script.chmod(0o755)
    _rewrite_console_scripts(prefix)
    relocated = tmp_path / "prefix with spaces"
    shutil.move(prefix, relocated)
    result = subprocess.run(
        [str(relocated / "bin/example"), "argument with spaces"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "argument with spaces\n"


def test_toolcache_stdlib_attribution_does_not_claim_unrelated_libraries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    prefix = tmp_path / "toolcache"
    stdlib = prefix / "lib/python3.12"
    extension = stdlib / "lib-dynload/_ssl.cpython-312-x86_64-linux-gnu.so"
    extension.parent.mkdir(parents=True)
    extension.write_bytes(b"copied CPython extension")
    (prefix / "LICENSE.txt").write_text("Python license notice")
    app = tmp_path / "app"
    component = _copied_component(
        extension,
        app,
        prefix,
        python_stdlib=stdlib,
        python_executable=prefix / "bin/python3.12",
        python_version="3.12.12",
    )
    assert component["package"] == "CPython"
    assert component["version"] == "3.12.12"
    assert (app / component["license"]).read_text() == "Python license notice"
    unrelated = prefix / "lib/libffi.so.8"
    unrelated.write_bytes(b"third-party library")
    with pytest.raises(RuntimeContractError, match="no package provenance"):
        _copied_component(
            unrelated,
            app,
            prefix,
            python_stdlib=stdlib,
            python_executable=prefix / "bin/python3.12",
            python_version="3.12.12",
        )


def test_musl_addon_requires_package_selector_and_native_edge(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(json.dumps(dict(name="@rollup/rollup-linux-x64-musl", libc=["musl"])))
    addon = tmp_path / "rollup.linux-x64-musl.node"
    assert _inactive_musl(addon, ["libc.so"], None)
    assert not _inactive_musl(addon, ["libc.so.6"], None)
    # Directory names alone do not exclude Codex static executables or its zsh.
    codex = tmp_path / "x86_64-unknown-linux-musl/bin/codex"
    assert not _inactive_musl(codex, [], None)
    assert not _inactive_musl(codex.with_name("zsh"), ["libc.so.6"], "/lib64/ld-linux-x86-64.so.2")


def test_native_inventory_distinguishes_build_objects_libraries_and_static_pie() -> None:
    assert (
        _native_role(
            ".venv/lib/python3.12/config-3.12-x86_64-linux-gnu/python.o",
            "Type: REL (Relocatable file)",
            inactive=False,
        )
        == "build object"
    )
    assert (
        _native_role(
            ".venv/lib/libicudata.so.74",
            "Type: DYN (Shared object file)",
            inactive=False,
        )
        == "runtime library"
    )
    assert (
        _native_role(
            "vendor/x86_64-unknown-linux-musl/bin/codex",
            "Type: DYN (Position-Independent Executable file)",
            inactive=False,
        )
        == "runtime entrypoint"
    )
    assert (
        _native_role(
            ".venv/bin/ruff",
            "Type: DYN (Position-Independent Executable file)",
            inactive=False,
        )
        == "development tool"
    )


def test_tk_omission_removes_stale_payload_and_retains_required_extensions(tmp_path: Path) -> None:
    from planner.environments.runtime_packaging import _exclude_tkinter, _stdlib_ignore

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    excluded = (
        "tkinter/__init__.py",
        "tkinter/ttk.py",
        "lib-dynload/_tkinter.cpython-312-x86_64-linux-gnu.so",
    )
    retained = (
        "lib-dynload/_ssl.cpython-312-x86_64-linux-gnu.so",
        "lib-dynload/_tkinter_extra.cpython-312-x86_64-linux-gnu.so",
        "turtle.py",
        "idlelib/__init__.py",
        "os.py",
    )
    # A prior package attempt leaves these files in the destination. Copy-ignore
    # alone does not remove them, which would leave Tk in the sealed inventory.
    for relative in (*excluded, *retained):
        for root in (source, destination):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
    removed = _exclude_tkinter(source, destination)
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=_stdlib_ignore(source))
    assert set(removed) == set(excluded)
    assert all(not (destination / relative).exists() for relative in excluded)
    assert all((destination / relative).is_file() for relative in retained)
    assert all((source / relative).is_file() for relative in (*excluded, *retained))


def test_physical_tk_library_refuses_even_without_any_dependency_edge(tmp_path: Path) -> None:
    from planner.environments.runtime_packaging import _assert_no_tkinter_payload

    library = tmp_path / "runtime/unused/libtcl8.6.so.0"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"stale unreferenced library")
    with pytest.raises(RuntimeContractError, match="excluded Tcl/Tk native file"):
        _assert_no_tkinter_payload(tmp_path)


def test_development_tool_role_does_not_hide_executable_loader_context() -> None:
    from planner.environments.runtime_packaging import _elf_executable

    header = "Type: DYN (Position-Independent Executable file)"
    relative = ".venv/lib/python3.12/site-packages/ruff/ruff"
    assert _native_role(relative, header, inactive=False) == "development tool"
    # An executable remains a separate loader root even under the stdlib prefix.
    # The packager uses this predicate, not the inventory's descriptive role.
    assert _elf_executable(header, "/lib64/ld-linux-x86-64.so.2")
    assert _elf_executable(header, None)
    assert _elf_executable("Type: EXEC (Executable file)", None)
    assert not _elf_executable("Type: DYN (Shared object file)", None)
