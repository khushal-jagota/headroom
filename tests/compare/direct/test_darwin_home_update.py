"""A populated Mac service/installer witness reuses the existing installed-home fixture.

Commands and native process observations are recording dependencies. No service,
health server, app script, provider or native Mac call executes on this host.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit21_metrics_collectors import _environment
from tests.unit.test_mac_home_install import home, loaded, prepared_attempt  # noqa: F401

from planner.platform_runtime.linux import update_installer as installer

ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "server/environments/updates/darwin/testing/compare.ts"
NODE = shutil.which("node") or "node"
LABEL = "com.panels.proof"
FROM, TO = "a" * 40, "b" * 40


def seed(root: Path, template: Path) -> dict[str, dict[str, Any]]:
    shutil.copytree(template, root)
    jobs = {}
    for path in (root / "agents").glob("*.plist"):
        # The existing fixture owns these values. Rebind only its installation root.
        text = path.read_text().replace(str(template), str(root))
        path.write_text(text)
        jobs[path.stem] = plistlib.loads(path.read_bytes())
    identity = root / "data/mac-home-installation.json"
    identity.write_text(identity.read_text().replace(str(template), str(root)))
    prepared_attempt(root)
    snapshot = root / "data/backups/snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "database.sqlite").write_text("record before the update")
    (root / "data/planner.db").write_text("record before the update")
    attempt = root / "data/update-attempt.json"
    value = json.loads(attempt.read_text())
    value["backup"] = str(snapshot)
    attempt.write_text(json.dumps(value))
    (root / "data/update-barrier.json").write_text("proof\n")
    return jobs


class World:
    def __init__(self, root: Path, scenario: str) -> None:
        self.root, self.scenario = root, scenario
        self.jobs = {
            LABEL + suffix: pid
            for suffix, pid in (
                ("", 123),
                (".backup", 125),
                (".maintenance", 126),
                (".stall-watchdog", 127),
            )
        }
        self.inventory = {pid: (1, os.getuid(), 1, pid) for pid in self.jobs.values()}
        if scenario == "rollback":
            self.inventory[124] = (126, os.getuid(), 1, 124)
        self.generation, self.child_reads = 1, 0
        self.retired_child, self.foreign = False, False
        self.events: list[list[str]] = []

    def process(self, pid: int) -> tuple[int, int, int, int] | None:
        if pid == 124 and self.retired_child:
            self.child_reads += 1
            # Same birth with changed parent/UID remains alive. A reused PID is retired.
            return (1, os.getuid() + 1, 1 if self.child_reads == 1 else 2, 124)
        return self.inventory.get(pid)

    def run(self, argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        code, out, err = 0, "", ""
        if argv[0] == "/bin/launchctl":
            operation = argv[1]
            label = argv[-1].rsplit("/", 1)[-1]
            if operation == "print":
                if label in self.jobs:
                    out = loaded(self.root, label, self.jobs[label])
                    if self.foreign and label == LABEL:
                        out = out.replace(str(self.root / "app/bin/panels"), "/foreign/panels")
                else:
                    code, err = 1, "Could not find service"
            elif operation == "bootout":
                self.events.append([operation, label])
                if self.scenario == "partial" and label == LABEL:
                    raise subprocess.CalledProcessError(1, argv, stderr="owned stop refused")
                pid = self.jobs.pop(label)
                self.inventory.pop(pid)
                if 124 in self.inventory and label.endswith(".maintenance"):
                    self.retired_child = True
                    self.inventory.pop(124)
            elif operation == "bootstrap":
                label = Path(argv[-1]).stem
                self.events.append([operation, label])
                if self.scenario == "auxiliary" and label.endswith(".backup"):
                    raise subprocess.CalledProcessError(1, argv, stderr="auxiliary refused")
                pid = {"": 123, ".backup": 125, ".maintenance": 126, ".stall-watchdog": 127}[
                    label.removeprefix(LABEL)
                ]
                self.generation += 1
                self.jobs[label] = pid
                self.inventory[pid] = (1, os.getuid(), self.generation, pid)
                if (
                    label == LABEL
                    and json.loads((self.root / "app/manifest.json").read_text())["app_sha"] == TO
                ):
                    (self.root / "data/planner.db").write_text("candidate committed work")
            else:
                raise AssertionError(argv)
        elif "--snapshot" in argv:
            assert len(options["pass_fds"]) == 1
            self.events.append(["restore"])
            shutil.copyfile(
                self.root / "data/backups/snapshot/database.sqlite", self.root / "data/planner.db"
            )
        else:
            # The Python reference validates its external interpreter metadata.
            assert argv[1:4] == ["-I", "-S", "-B"] and "sys.version_info" in argv[-1]
        return subprocess.CompletedProcess(argv, code, out, err)

    def health(self, _url: str, sha: str, _timeout: float) -> bool:
        self.events.append(
            [
                "health",
                sha,
                "closed" if (self.root / "data/update-barrier.json").exists() else "open",
            ]
        )
        return self.scenario != "rollback" or sha == FROM


def outcome(root: Path, world: World, code: int) -> dict[str, Any]:
    record = json.loads((root / "data/update-attempt.json").read_text())
    return {
        "code": code,
        "events": world.events,
        "state": record["state"],
        "steps": sorted(record["steps"]),
        "detail_present": bool(record.get("detail")),
        "app": json.loads((root / "app/manifest.json").read_text())["app_sha"],
        "staged": (root / (".app-next-" + TO)).exists(),
        "previous": (root / (".app-previous-" + FROM)).exists(),
        "database": (root / "data/planner.db").read_text(),
        "barrier": (root / "data/update-barrier.json").exists(),
        "jobs": sorted(world.jobs),
        "retired_birth_checked": world.child_reads >= 2,
    }


def test_darwin_home_update_matches_populated_python(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    template = request.getfixturevalue("home")
    assert isinstance(template, Path)
    began = time.perf_counter()
    cases: list[dict[str, Any]] = []
    expected, python_records = [], []
    for scenario in ("success", "partial", "rollback", "auxiliary"):
        root = tmp_path / scenario
        jobs = seed(root, template)
        world = World(root, scenario)
        arguments = argparse.Namespace(
            current_root=root,
            attempt_id="proof",
            service=f"gui/{os.getuid()}/{LABEL}",
            health_url="unused",
            health_timeout_seconds=1,
        )
        with (
            patch.object(
                installer,
                "sys",
                type(
                    "Mac",
                    (),
                    {"platform": "darwin", "executable": sys.executable, "stderr": sys.stderr},
                )(),
            ),
            patch.object(subprocess, "run", world.run),
            patch.object(
                installer, "mac_processes", lambda _deadline, _world=world: dict(_world.inventory)
            ),
            patch.object(installer, "mac_process_identity", world.process),
            patch.object(installer, "healthy", world.health),
            patch("planner.platform_runtime.linux.update_installer.time.sleep", return_value=None),
            patch.object(
                installer.Installation,
                "capture_interruptions",
                lambda _self, _world=world: _world.events.append(["capture"]),
            ),
            redirect_stderr(StringIO()),
        ):
            if scenario == "success":
                world.foreign = True
                subject = installer.MacService(root, arguments.service)
                with pytest.raises(ValueError):
                    subject.stop()
                assert world.events == []
                world.foreign = False
                # The same success case upgrades an older two-job installation.
                for suffix in (".maintenance", ".stall-watchdog"):
                    (root / "agents" / (LABEL + suffix + ".plist")).unlink()
                    world.inventory.pop(world.jobs.pop(LABEL + suffix))
            code = installer.install(arguments)
        expected.append(outcome(root, world, code))
        python_records.append(json.loads((root / "data/update-attempt.json").read_text()))
        shutil.rmtree(root)
        seed(root, template)
        cases.append({"root": str(root), "scenario": scenario, "jobs": jobs})
    Path.home().mkdir(parents=True, exist_ok=True)
    spec = tmp_path / "darwin-updates.json"
    spec.write_text(json.dumps({"cases": cases, "account_home": str(Path.home())}))
    result = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "-e",
            "import{readFileSync}from'node:fs';const m=await import(process.argv[1]);"
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8'));"
            "process.stdout.write(JSON.stringify(await m.darwin_updates(input)));",
            COMPANION.as_uri(),
            str(spec),
        ],
        cwd=ROOT,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    legacy_identity = json.loads(
        (Path(cases[0]["root"]) / "data/mac-home-installation.json").read_text()
    )
    for key in ("uid", "root", "label", "home_plist", "backup_plist"):
        assert actual["v2_identity"][key] == legacy_identity[key]
    report = {
        "seconds": time.perf_counter() - began,
        "outcomes": expected,
        "typescript_outcomes": actual["outcomes"],
        "python_attempts": python_records,
        "typescript_attempts": actual["records"],
        "identity_difference": {
            "python": legacy_identity,
            "typescript": actual["v2_identity"],
        },
        "boundary": (
            "Existing installed-home fixture and recording native service/process/health "
            "dependencies. No new host."
        ),
        "diagnostics": (
            "Raw attempt details and timestamps are retained. Common state, steps, data, "
            "moves, barrier, jobs and control order compare exactly."
        ),
    }
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        Path(directory).mkdir(parents=True, exist_ok=True)
        (Path(directory) / "darwin-home-update.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual["outcomes"] == expected
    assert actual["v2_identity"]["version"] == 2
    assert actual["v2_identity"]["installer_node"] == str(
        Path(shutil.which("node") or "node").resolve()
    )
