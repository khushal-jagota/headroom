"""New native boundary risks; all subprocesses here are local software fixtures."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from planner.environments.mac_app.build import build_app, runtime_definition
from planner.executor.cli import executor_group


def test_preview_never_traverses_selected_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    original_stat = os.stat
    original_resolve = Path.resolve

    def guarded_stat(path: Any, *args: Any, **kwargs: Any) -> object:
        if str(path).startswith("/absent"):
            raise AssertionError("preview inspected a selected path")
        return original_stat(path, *args, **kwargs)

    def guarded_resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        if str(path).startswith("/absent"):
            raise AssertionError("preview resolved a selected path")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(os, "stat", guarded_stat)
    result = CliRunner().invoke(
        executor_group,
        ["permissions", "--workspace", "/absent workspace", "--output", "/absent output"],
    )
    assert result.exit_code == 0, result.output
    assert "Preview only" in result.output


def test_explicit_check_preserves_files_and_reports_denial(tmp_path: Path) -> None:
    workspace, output = tmp_path / "Workspace", tmp_path / "Output"
    workspace.mkdir()
    output.mkdir()
    original = output / "keep.txt"
    original.write_text("keep")
    args = ["permissions", "--workspace", str(workspace), "--output", str(output), "--check"]
    result = CliRunner().invoke(executor_group, args)
    assert result.exit_code == 0, result.output
    assert list(output.iterdir()) == [original]
    assert original.read_text() == "keep"
    workspace.rmdir()
    result = CliRunner().invoke(executor_group, args)
    assert result.exit_code != 0
    assert "No retry or permission reset occurred" in result.output


def test_native_trampoline_keeps_fixed_runtime_and_argument_boundaries(tmp_path: Path) -> None:
    compiler = shutil.which("cc")
    assert compiler is not None, "native launcher software proof requires a C compiler"
    fixture = tmp_path / 'runtime with spaces " and \U0001f408'
    fixture.write_text(
        "#!/usr/bin/python3\nimport sys,json,os\n"
        'print(json.dumps({"argv":sys.argv[1:],"pid":os.getpid()}))\nsys.exit(23)\n'
    )
    fixture.chmod(0o700)
    (tmp_path / "runtime_path.h").write_text(runtime_definition(fixture))
    source = Path(__file__).parents[2] / "src/planner/environments/mac_app/launcher.c"
    binary = tmp_path / "Panels"
    subprocess.run(
        [
            compiler,
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(tmp_path),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
    )
    assert b"\0" + os.fsencode(fixture) + b"\0" in binary.read_bytes()
    args = ["executor", "permissions", "--workspace", "/a folder/with spaces", "--output", 'x"y']
    with subprocess.Popen(
        [str(binary), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PATH": "/nonexistent"},
    ) as process:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 23, stderr
        payload = json.loads(stdout)
        assert payload["pid"] == process.pid
        assert payload["argv"] == ["-I", "-B", "-m", "planner.executor.entrypoint", *args]
    fixture.unlink()
    assert subprocess.run([str(binary)], capture_output=True).returncode != 0


def test_builder_refuses_live_app_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    with pytest.raises(ValueError, match="outside the installed app"):
        build_app(tmp_path, tmp_path / "Applications/Panels.app", "a" * 40)
    assert not (tmp_path / "Applications").exists()


def test_signature_failure_stops_admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import plistlib

    from planner.environments.mac_app import verify
    from planner.environments.mac_app.build import bundle_metadata

    app = tmp_path / "Panels.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(bundle_metadata("fixture")))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)

    def invalid(*arguments: str) -> str:
        raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(verify, "_codesign", invalid)
    with pytest.raises(subprocess.CalledProcessError):
        verify.verify_app(app, "a" * 40)


def test_update_checks_both_designated_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plistlib

    from planner.environments.mac_app import verify
    from planner.environments.mac_app.build import bundle_metadata

    apps = [tmp_path / "candidate.app", tmp_path / "previous.app"]
    for app in apps:
        (app / "Contents").mkdir(parents=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(bundle_metadata("fixture")))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: None)
    calls: list[tuple[str, ...]] = []

    def codesign(*arguments: str) -> str:
        calls.append(arguments)
        if "-r-" in arguments:
            return "designated => " + (
                "candidate-rule" if arguments[-1] == str(apps[0]) else "old-rule"
            )
        return "Authority=Apple Development: fixture\n"

    monkeypatch.setattr(verify, "_codesign", codesign)
    verify.verify_app(apps[0], "a" * 40, apps[1])
    assert ("--verify", "--strict", "-R", "=old-rule", str(apps[0])) in calls
    assert ("--verify", "--strict", "-R", "=candidate-rule", str(apps[1])) in calls
    assert any('certificate leaf = H"' in argument for call in calls for argument in call)


def test_production_build_and_verification_use_selected_interpreter_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform
    import plistlib

    from planner.environments.mac_app import verify

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    python = tmp_path / "Production/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    app = tmp_path / "candidate.app"
    bundle_id = "com.panels.executor.production.app"
    calls: list[list[str]] = []

    def run(arguments: list[str], **kwargs: Any) -> None:
        calls.append(arguments)
        if arguments[0] == "/usr/bin/xcrun":
            header = Path(arguments[arguments.index("-I") + 1]) / "runtime_path.h"
            assert header.read_text() == runtime_definition(python)
            Path(arguments[-1]).write_bytes(b"fixture\0" + os.fsencode(python) + b"\0")

    monkeypatch.setattr(subprocess, "run", run)
    build_app(
        tmp_path,
        app,
        "fixture",
        python=python,
        bundle_id=bundle_id,
        installed_app=tmp_path / "Applications/Panels Production.app",
    )
    metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert metadata["CFBundleIdentifier"] == bundle_id
    monkeypatch.setattr(
        verify,
        "_codesign",
        lambda *args: (
            "designated => production-rule"
            if "-r-" in args
            else "Authority=Apple Development: fixture\n"
        ),
    )
    verify.verify_app(app, "a" * 40, bundle_id=bundle_id, python=python)
    with pytest.raises(ValueError, match="different Panels bundle identity"):
        verify.verify_app(app, "a" * 40)
    with pytest.raises(ValueError, match="selected Python"):
        verify.verify_app(app, "a" * 40, bundle_id=bundle_id, python=tmp_path / "isolated/python")
    # A suffix of the embedded path must not satisfy an exact interpreter check.
    with pytest.raises(ValueError, match="selected Python"):
        verify.verify_app(app, "a" * 40, bundle_id=bundle_id, python=Path("/venv/bin/python"))
    before = len(calls)
    with pytest.raises(FileExistsError):
        build_app(tmp_path, app, "fixture", python=python, bundle_id=bundle_id)
    assert len(calls) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("python", Path("relative/python")),
        ("installed_app", Path("relative.app")),
        ("bundle_id", "bad identity"),
    ],
)
def test_build_rejects_invalid_inputs_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    output = tmp_path / "candidate.app"
    with pytest.raises(ValueError):
        build_app(tmp_path, output, "fixture", **{field: value})
    assert not output.exists()


def test_build_protects_selected_installed_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    installed = tmp_path / "Applications/Panels Production.app"
    with pytest.raises(ValueError, match="outside the installed app"):
        build_app(tmp_path, installed / "nested.app", "fixture", installed_app=installed)
    assert not installed.exists()
