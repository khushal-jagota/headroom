"""Instrument-only local packaging proof; this is not Mac launchd evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from planner.environments.resident.contracts import ExecutorSourceClosure, ResidentPackage
from planner.environments.resident.packaging import (
    build_source_package,
    prepare_runtime,
    validate_runtime,
)
from planner.environments.resident.paths import resident_paths


def test_launcher_uses_isolated_committed_source_and_preserves_home(tmp_path: Path) -> None:
    source = tmp_path / "source with spaces"
    module = source / "src/executor_fixture"
    module.mkdir(parents=True)
    (module / "__main__.py").write_text(
        "import json,os,sys\n"
        "print(json.dumps({'argv':sys.argv[1:],'home':os.environ['HOME'],'source':'committed'}))\n"
    )
    (source / "requirements.txt").write_text("")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
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
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    package = ResidentPackage(
        sha,
        1,
        1,
        "darwin",
        "arm64",
        "3.12",
        ExecutorSourceClosure(
            ("src/executor_fixture/__main__.py", "requirements.txt"),
            "executor_fixture",
            "requirements.txt",
        ),
    )
    home = tmp_path / "Account With Spaces"
    paths = resident_paths(home)
    app = build_source_package(source, package, paths.app)

    def build(candidate: Path, runtime: Path, spec: ResidentPackage) -> None:
        (runtime / "bin").mkdir()
        (runtime / "bin/python").symlink_to(sys.executable)

    prepared = prepare_runtime(app, package, paths, build)
    unrelated = tmp_path / "unrelated"
    (unrelated / "executor_fixture").mkdir(parents=True)
    (unrelated / "executor_fixture/__main__.py").write_text("raise RuntimeError('hostile source')")
    environment = dict(os.environ, HOME=str(home), PYTHONPATH=str(unrelated))
    result = subprocess.run(
        [str(app / "bin/panels"), "executor", "run"],
        cwd=unrelated,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "argv": ["executor", "run", "--application-revision", sha],
        "home": str(home),
        "source": "committed",
    }

    for arguments in (
        ["serve"],
        ["executor", "run", "--application-revision", sha],
        ["executor", "run", "--application-revision=" + sha],
    ):
        refused = subprocess.run(
            [str(app / "bin/panels"), *arguments],
            cwd=unrelated,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert refused.returncode != 0
        assert not refused.stdout
    validate_runtime(prepared)  # Execution leaves the declared source tree unchanged.
