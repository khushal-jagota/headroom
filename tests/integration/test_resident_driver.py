"""Clean-process bootstrap instruments, without native install or host contact."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tarfile
from dataclasses import asdict
from pathlib import Path

from planner.environments.resident.contracts import ExecutorSourceClosure, ResidentPackage
from planner.environments.resident.transport import (
    _REMOTE_BOOTSTRAP,
    _TOOL_SHARED,
    build_delivery_archive,
)


def test_exact_bundle_bootstraps_without_preinstalled_panels_or_hidden_checkout(
    tmp_path: Path,
) -> None:
    checkout = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    paths = set(_TOOL_SHARED) | {
        str(path.relative_to(checkout))
        for path in (checkout / "src/planner/environments/resident").rglob("*.py")
    }
    for name in paths:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((checkout / name).read_bytes())
    (source / "src/example").mkdir(parents=True)
    (source / "src/example/runner.py").write_text("pass\n")
    (source / "requirements.lock").write_text("")
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
            "exact bootstrap fixture",
        ],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    package = ResidentPackage(
        revision,
        1,
        1,
        "darwin",
        "arm64",
        "3.12",
        ExecutorSourceClosure(
            ("src/example/runner.py", "requirements.lock"), "example.runner", "requirements.lock"
        ),
    )
    manifest = tmp_path / "spec"
    manifest.write_text(json.dumps(asdict(package)))
    # Dirty checkout bytes cannot enter the committed tooling or app bundle.
    (source / "src/example/runner.py").write_text("raise RuntimeError('dirty')\n")
    archive = build_delivery_archive(source, manifest, revision)
    tree = tmp_path / "delivered"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(tree, filter="data")
    assert (tree / "app/src/example/runner.py").read_text() == "pass\n"
    tools = tree / "tools/src"
    # First stage genuinely imports using no third-party site-packages.
    imports = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from planner.environments.resident import cli,driver,native_builder,packaging;"
        "print(driver.__file__)"
    )
    first = subprocess.run(
        [sys.executable, "-I", "-B", "-S", "-c", imports, str(tools)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert first.stdout.strip() == str(tools / "planner/environments/resident/driver.py")
    # Once target dependencies exist, owner IPC and the production compatibility
    # checker resolve only from delivered tooling, not the local checkout.
    imports = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from planner.environments.resident import control,service;"
        "from planner.executor import preflight;print(preflight.__file__)"
    )
    second = subprocess.run(
        [sys.executable, "-I", "-B", "-c", imports, str(tools)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert second.stdout.strip() == str(tools / "planner/executor/preflight.py")
    # Execute the exact SSH bootstrap byte stream on this Linux instrument. It
    # extracts into the test account root, imports cleanly and refuses native work.
    home = tmp_path / "instrument-home"
    home.mkdir()
    config = [
        "--python",
        sys.executable,
        "--requested-sha",
        revision,
        "--executor-ssh-host",
        "vps",
        "--panels-server-url",
        "https://panels.example",
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-S",
            "-c",
            _REMOTE_BOOTSTRAP,
            base64.b64encode(json.dumps(config).encode()).decode(),
        ],
        input=archive,
        cwd=tmp_path,
        env=dict(os.environ, HOME=str(home)),
        capture_output=True,
    )
    assert result.returncode == 2, result.stderr.decode()
    assert "actual Mac owner's HOME" in json.loads(result.stdout)["error"]
    assert len(list((home / "Deployments/PanelsExecutor/staging").iterdir())) == 1
