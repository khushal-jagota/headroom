from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from planner.environments.resident.contracts import (
    ExecutorSourceClosure,
    ResidentError,
    ResidentPackage,
)
from planner.environments.resident.packaging import (
    build_source_package,
    prepare_runtime,
    validate_package,
    validate_runtime,
)
from planner.environments.resident.paths import resident_paths


def _repository(tmp_path: Path) -> tuple[Path, ResidentPackage]:
    source = tmp_path / "repo"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "src/executor_fixture").mkdir(parents=True)
    (source / "src/executor_fixture/__main__.py").write_text("print('committed')\n")
    (source / "executor-requirements.txt").write_text("example==1.0\n")
    (source / "secret").write_text("not allowed")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    return source, ResidentPackage(
        sha,
        1,
        1,
        "darwin",
        "arm64",
        "3.12",
        ExecutorSourceClosure(
            ("src/executor_fixture/__main__.py", "executor-requirements.txt"),
            "executor_fixture",
            "executor-requirements.txt",
        ),
    )


def _builder(app: Path, runtime: Path, package: ResidentPackage) -> None:
    assert app.is_dir() and package.target_os == "darwin"
    (runtime / "bin").mkdir()
    python = runtime / "bin/python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)


def test_exact_committed_allowlist_excludes_dirty_files_and_secrets(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    (source / "src/executor_fixture/__main__.py").write_text("dirty")
    app = build_source_package(source, package, tmp_path / "candidate")
    assert (app / "src/executor_fixture/__main__.py").read_text() == "print('committed')\n"
    assert not (app / "secret").exists()
    assert not (app / ".venv").exists()


@pytest.mark.parametrize(
    "bad", ["../secret", "/secret", "a/../secret", ".venv/bin/python", "src/planner/core/server.py"]
)
def test_rejects_unsafe_or_server_source_paths(tmp_path: Path, bad: str) -> None:
    _, package = _repository(tmp_path)
    with pytest.raises(ResidentError):
        validate_package(
            replace(package, closure=replace(package.closure, files=package.closure.files + (bad,)))
        )


def test_missing_entrypoint_and_symlink_fail(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    with pytest.raises(ResidentError, match="entrypoint"):
        validate_package(
            replace(package, closure=replace(package.closure, entrypoint_module="missing"))
        )
    (source / "link").symlink_to("secret")
    subprocess.run(["git", "-C", str(source), "add", "link"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "link",
        ],
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    package = replace(
        package,
        application_revision=sha,
        closure=replace(package.closure, files=package.closure.files + ("link",)),
    )
    with pytest.raises(ResidentError, match="non-regular"):
        build_source_package(source, package, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_runtime_built_at_permanent_path_and_bound_to_inputs(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    app = build_source_package(source, package, tmp_path / "candidate")
    paths = resident_paths(tmp_path / "Account With Spaces")
    locations: list[Path] = []

    def build(candidate: Path, runtime: Path, spec: ResidentPackage) -> None:
        locations.append(runtime)
        _builder(candidate, runtime, spec)

    prepared = prepare_runtime(app, package, paths, build)
    assert locations == [prepared.runtime]
    assert prepared.runtime.parent == paths.runtimes
    assert prepare_runtime(app, package, paths, build) == prepared
    assert len(locations) == 1
    (app / "executor-requirements.txt").write_text("changed==2.0")
    with pytest.raises(ResidentError, match="input bytes changed"):
        validate_runtime(prepared)


def test_incomplete_runtime_cannot_be_reused(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    app = build_source_package(source, package, tmp_path / "candidate")
    paths = resident_paths(tmp_path / "home")

    def fail(candidate: Path, runtime: Path, spec: ResidentPackage) -> None:
        raise ResidentError("native build failed")

    with pytest.raises(ResidentError, match="native build failed"):
        prepare_runtime(app, package, paths, fail)
    with pytest.raises(ResidentError, match="preparation marker"):
        prepare_runtime(app, package, paths, _builder)


def test_source_manifest_tampering_before_preparation_refuses(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    app = build_source_package(source, package, tmp_path / "candidate")
    (app / "src/executor_fixture/__main__.py").write_text("changed")
    with pytest.raises(ResidentError, match="input bytes changed"):
        prepare_runtime(app, package, resident_paths(tmp_path / "home"), _builder)


def test_symlinked_destination_parent_refused_before_writes(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    actual = tmp_path / "actual"
    actual.mkdir()
    (tmp_path / "link").symlink_to(actual, target_is_directory=True)
    with pytest.raises(ResidentError, match="symlink"):
        build_source_package(source, package, tmp_path / "link/candidate")
    assert not (actual / "candidate").exists()


def test_symlinked_source_parent_and_changed_launcher_refused(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    app = build_source_package(source, package, tmp_path / "candidate")
    prepared = prepare_runtime(app, package, resident_paths(tmp_path / "home"), _builder)
    (app / "bin/panels").write_text("changed")
    with pytest.raises(ResidentError, match="entrypoint changed"):
        validate_runtime(prepared)
    moved = tmp_path / "moved"
    (app / "src").rename(moved)
    (app / "src").symlink_to(moved, target_is_directory=True)
    with pytest.raises(ResidentError, match="symlink"):
        prepare_runtime(app, package, resident_paths(tmp_path / "home"), _builder)


def test_revision_must_name_commit_object(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    tree = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"],
        text=True,
    ).strip()
    with pytest.raises(ResidentError, match="commit object"):
        build_source_package(
            source, replace(package, application_revision=tree), tmp_path / "candidate"
        )


def test_moved_native_runtime_is_not_accepted(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    app = build_source_package(source, package, tmp_path / "candidate")
    prepared = prepare_runtime(app, package, resident_paths(tmp_path / "home"), _builder)
    moved = tmp_path / "moved-runtime"
    prepared.runtime.rename(moved)
    with pytest.raises(ResidentError, match="input identity"):
        validate_runtime(replace(prepared, runtime=moved))


def test_fresh_app_reuses_native_runtime_without_rebuilding(tmp_path: Path) -> None:
    source, package = _repository(tmp_path)
    paths = resident_paths(tmp_path / "home")
    first = build_source_package(source, package, tmp_path / "first")
    original = prepare_runtime(first, package, paths, _builder)
    second = build_source_package(source, package, tmp_path / "second")

    def never_build(app: Path, runtime: Path, spec: ResidentPackage) -> None:
        pytest.fail("existing native runtime must not be rebuilt")

    reused = prepare_runtime(second, package, paths, never_build)
    assert reused.runtime == original.runtime
    assert reused.app != original.app
    validate_runtime(reused)


@pytest.mark.parametrize(
    "extra", ["src/injected.py", "src/executor_fixture/__init__.py", "src/executor_fixture/unused"]
)
def test_runtime_validation_rejects_undeclared_source(tmp_path: Path, extra: str) -> None:
    source, package = _repository(tmp_path)
    app = build_source_package(source, package, tmp_path / "candidate")
    prepared = prepare_runtime(app, package, resident_paths(tmp_path / "home"), _builder)
    target = app / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("raise RuntimeError('undeclared')")
    with pytest.raises(ResidentError, match="undeclared"):
        validate_runtime(prepared)
