"""Account ownership is distinct from the existing destructive server/Mac publishers.

One export-only Node witness compares populated filesystem state with fresh Python.
No network, service, provider, or new connected test host is required.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from planner.skill_sources import account_skill_provisioning_conflict, provision_account_skills
from tests.support.synthetic_homes import create_synthetic_homes

ROOT = Path(__file__).resolve().parents[3]


def _reference(root: Path) -> dict[str, Any]:
    first, second = root / "first", root / "second"
    homes = tuple(root / name for name in ("hermes", "codex", "claude"))
    preflight = account_skill_provisioning_conflict(first, homes)
    untouched = sorted(path.name for path in root.iterdir())
    published = provision_account_skills(first, homes)
    link = homes[0] / "skills/panels"
    inode = link.lstat().st_ino
    (homes[0] / "skills/panels-worker-personal-task").symlink_to(first / "skills/panels-worker-personal-task")
    (homes[0] / "skills/gone").symlink_to(first / "skills/gone")
    (homes[0] / "skills/user").mkdir()
    repeated = provision_account_skills(first, homes)
    stable = inode == link.lstat().st_ino
    other = provision_account_skills(second, (root / "unused", *homes))
    legacy = root / "legacy"
    legacy.mkdir()
    (legacy / "skills").symlink_to(first / "skills")
    converted = provision_account_skills(first, (legacy,))
    (homes[0] / "skills/panels-rollover").symlink_to(root / "foreign")
    conflict = provision_account_skills(first, homes)
    def tree(path: Path) -> list[Any]:
        values = []
        for entry in sorted(path.iterdir()):
            name = str(entry)[len(str(root)):]
            if entry.is_symlink():
                values.append([name, "link", str(entry.readlink()).replace(str(root), "<root>")])
            elif entry.is_dir():
                values.extend([[name, "dir"], *tree(entry)])
            else:
                values.append([name, "file"])
        return values
    return dict(preflight=preflight, untouched=untouched, published=published, repeated=repeated,
                stable=stable, other=other.replace(str(root), "<root>") if other else None,
                converted=converted, conflict=conflict.replace(str(root), "<root>") if conflict else None,
                tree=tree(root))


def test_home_account_ownership_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    roots = [tmp_path / name for name in ("py", "ts")]
    for root in roots:
        for owner in ("first", "second"):
            for name in ("panels", "panels-worker", "panels-worker-personal-task"):
                folder = root / owner / "skills" / name
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text(name)
    expected = _reference(roots[0])
    node = shutil.which("node")
    assert node
    environment = {"PATH": str(Path(node).parent) + os.pathsep + os.defpath, **create_synthetic_homes(tmp_path)}
    run = subprocess.run([node, "--input-type=module", "-e",
        "import {account_parity} from './server/skills/testing/account_parity.ts'; console.log(JSON.stringify(account_parity(process.argv[1])));",
        str(roots[1])], cwd=ROOT, env=environment, text=True, capture_output=True, timeout=20, check=True)
    assert json.loads(run.stdout) == expected
    elapsed = time.perf_counter() - started
    request.node.user_properties.append(("elapsed_seconds", round(elapsed, 3)))
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "home-account-skills.json").write_text(json.dumps(dict(equal=True, elapsed_seconds=elapsed, observations=expected), indent=2))
