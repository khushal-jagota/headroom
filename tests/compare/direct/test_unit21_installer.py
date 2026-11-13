"""One populated algorithm sequence and one configured, invocation-owned lifetime.

Service and health are recording dependencies. App scripts never execute. The
configured lifetime executes the real classifier and disclosed Node witnesses.
"""

from __future__ import annotations

import argparse
import errno
import io
import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from contextlib import redirect_stderr
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.integration.test_update_installer import (
    ATTEMPT_ID,
    FROM_SHA,
    OLDER_SHA,
    TO_SHA,
    Scratch,
    write_app,
    write_attempt,
)
from tests.support.synthetic_homes import create_synthetic_homes

from planner.environments import update_installer as installer

ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "server/environments/updates/installer/testing/compare.ts"
NOW = datetime(2026, 11, 13, 12, 0, 0, 123000, tzinfo=UTC)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> FrozenDatetime:
        return cls(2026, 11, 13, 12, 0, 0, 123000, tzinfo=tz)


def _tree(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        row = {
            "path": path.relative_to(root).as_posix(),
            "mode": stat.S_IMODE(info.st_mode),
        }
        if path.is_symlink():
            row.update(kind="symlink", target=os.readlink(path))
        elif path.is_dir():
            row.update(kind="directory")
        else:
            row.update(kind="file", bytes=path.read_bytes().hex())
        rows.append(row)
    return rows


def _seed(root: Path, scenario: str) -> Scratch:
    if root.exists():
        shutil.rmtree(root)
    (root / "data").mkdir(parents=True)
    scratch = Scratch(root, root.parent / "unused-state", 0)
    write_app(root / "app", FROM_SHA)
    write_app(root / f".app-next-{TO_SHA}", TO_SHA)
    write_app(root / f".app-previous-{OLDER_SHA}", OLDER_SHA)
    write_app(root / "app-previous", OLDER_SHA)
    snapshot = root / "data/backups/snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "database.sqlite").write_text("database before the update")
    (root / "data/planner.db").write_text("database written by the new version")
    write_attempt(
        scratch,
        backup=str(snapshot),
        state="done" if scenario == "stale" else "installing",
    )
    if scenario == "success":
        record = scratch.record()
        record["unknown"] = {
            "unicode": "雪",
            "large": 9007199254740993,
            "float": 1.0,
            "nan": float("nan"),
        }
        scratch.record_path.write_text(json.dumps(record))
    return scratch


def _python(scratch: Scratch, scenario: str) -> tuple[dict[str, Any], str]:
    events: list[list[str]] = []
    stderr, capture_detail = io.StringIO(), ""
    original_rename = Path.rename
    staged, app = scratch.root / f".app-next-{TO_SHA}", scratch.root / "app"

    def rename(path: Path, target: Any) -> Path:
        if scenario == "rename" and path == staged and Path(target) == app:
            raise OSError(
                errno.EIO, os.strerror(errno.EIO), str(path), None, str(target)
            )
        return original_rename(path, target)

    def command(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        nonlocal capture_detail
        if argv[1:4] == ["-I", "-m", "planner.environments.interruption_capture"]:
            assert argv[4:6] == [str(scratch.root / "data/planner.db"), ATTEMPT_ID]
            assert argv[6] == "0" * 32
            assert options["timeout"] == 2.0 and "check" not in options
            events.append(["capture"])
            if scenario == "rename":
                error = subprocess.TimeoutExpired(argv, 2.0)
                capture_detail = str(error)
                raise error
            return subprocess.CompletedProcess(argv, 7 if scenario == "rollback" else 0)
        expected = [
            str(app / "bin/panels"),
            "environment",
            "restore",
            "--snapshot",
            str(scratch.root / "data/backups/snapshot"),
            "--destination-db",
            str(scratch.root / "data/planner.db"),
            "--live-stopped",
        ]
        assert argv == expected, f"unadmitted subprocess: {argv}"
        assert options["check"] and options["timeout"] == 900.0
        events.append(["restore", argv[4], argv[6]])
        shutil.copyfile(Path(argv[4]) / "database.sqlite", argv[6])
        return subprocess.CompletedProcess(argv, 0)

    def healthy(_url: str, sha: str, _timeout: float) -> bool:
        events.append(["health", sha])
        return scenario != "rollback" or sha == FROM_SHA

    arguments = argparse.Namespace(
        current_root=scratch.root,
        attempt_id=ATTEMPT_ID,
        service="synthetic.service",
        health_url="http://127.0.0.1:0",
        health_timeout_seconds=3.0,
    )
    with (
        patch.object(installer, "datetime", FrozenDatetime),
        patch.object(
            installer.Installation,
            "systemctl",
            lambda _self, action: events.append(["service", action]),
        ),
        patch.object(installer, "healthy", healthy),
        patch.object(subprocess, "run", command),
        patch.object(uuid, "uuid4", lambda: uuid.UUID(int=0)),
        patch.object(Path, "rename", rename),
        redirect_stderr(stderr),
    ):
        code = installer.install(arguments)
    return {
        "code": code,
        "events": events,
        "stderr": stderr.getvalue(),
        "tree": _tree(scratch.root),
    }, capture_detail


def test_installer_algorithm_and_configured_paths(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Risk: swap/rollback parity or configured consumers lose data or escape ownership."""
    started = time.monotonic()
    cases, expected = [], []
    for scenario in ("success", "rollback", "rename", "stale"):
        root = tmp_path / scenario
        scratch = _seed(root, scenario)
        result, detail = _python(scratch, scenario)
        expected.append(result)
        _seed(root, scenario)
        cases.append(
            {"root": str(root), "scenario": scenario, "capture_detail": detail}
        )
    homes = create_synthetic_homes(tmp_path)
    # No inherited PLAN_* values, provider homes, loader hooks, or provider credentials.
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        **homes,
        "TMPDIR": str(tmp_path),
        "TMP": str(tmp_path),
        "TEMP": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "PLAN_CONFIG_PATH": str(tmp_path / "config.yaml"),
        "PLAN_DB_PATH": str(tmp_path / "unused.sqlite"),
        "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        "PLAN_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        "PLAN_SERVER_LIFECYCLE_LEASE_PATH": str(tmp_path / "lifecycle.lock"),
        "PLAN_APP_ROOT": str(tmp_path / "unused-app"),
        "PLAN_CREDENTIAL_KEY_PATH": str(tmp_path / "credential.key"),
        "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
    }
    (tmp_path / "config.yaml").write_text("{}\n")
    spec = tmp_path / "installer.json"
    spec.write_text(
        json.dumps(
            {
                "cases": cases,
                "configured_root": str(tmp_path / "configured"),
                "now_ms": int(NOW.timestamp() * 1000),
            }
        )
    )
    launcher = (
        "import{readFileSync}from'node:fs';"
        "const{installer_probe}=await import(process.argv[1]);"
        "const input=JSON.parse(readFileSync(process.argv[2],'utf8'));"
        "console.log(JSON.stringify(await installer_probe(input)));"
    )
    node_started = time.monotonic()
    child = subprocess.Popen(
        [
            shutil.which("node") or "node",
            "--input-type=module",
            "-e",
            launcher,
            COMPANION.as_uri(),
            str(spec),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=60)
        assert child.returncode == 0, stderr
        actual = json.loads(stdout)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=3)
    assert actual["canonical"] == expected
    report = {
        "complete": True,
        "canonical_scenarios": 4,
        **actual["configured"],
        "node_seconds": time.monotonic() - node_started,
        "body_seconds": time.monotonic() - started,
        "scope": (
            "Recording service/health and canonical capture/restore. "
            "Configured capture/restore are owned Node witnesses, "
            "not runtime or manifest proof."
        ),
    }
    output = request.config.getoption("compare_report_dir", default=None)
    if output:
        directory = Path(output)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit21-installer.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
