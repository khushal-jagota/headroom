"""Canonical generations and account publication, compared with fresh Python.

Unit 5 proves server-managed publication. It does not prove command snapshots,
generation reuse/refusal, or Mac worker custody around a contended local lock.
One export-only child and synthetic homes cover those distinct boundaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes

from planner.executor.configuration import ExecutorPaths
from planner.executor.protocol import CanonicalSkillSnapshot, CanonicalSkillSource
from planner.executor.skills import install_native_skill_snapshot

ROOT = Path(__file__).resolve().parents[3]


def _tree(path: Path, root: Path, prefix: str = "") -> list[Any]:
    result: list[Any] = []
    for entry in sorted(path.iterdir()):
        key = prefix + entry.name
        if entry.is_symlink():
            result.append([key, "link", os.readlink(entry).replace(str(root), "<root>")])
        elif entry.is_dir():
            result.extend([[key, "dir"], *_tree(entry, root, key + "/")])
        else:
            raw = entry.read_bytes()
            result.append([key, len(raw), hashlib.sha256(raw).hexdigest()])
    return result


def _prepare(root: Path) -> None:
    skills = root / "provider" / "skills"
    skills.mkdir(parents=True)
    (skills / "user-skill").mkdir()
    (skills / "user-skill" / "custom").write_text("unrelated", encoding="utf-8")
    (skills / "user-dead").symlink_to(root / "missing-user-skill")
    (skills / "panels-rollover").symlink_to(root / "state" / "skills" / "old" / "panels-rollover")
    (skills / ".panels-skill-00000000-0000-0000-0000-000000000000").symlink_to(
        root / "state" / "skills" / "old"
    )
    (skills / ".panels-skill-11111111-1111-1111-1111-111111111111").symlink_to(
        root / "user-temporary"
    )


def test_unit22_mac_skills(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)

    def snapshot(extra: str) -> CanonicalSkillSnapshot:
        names = ["panels", "panels-worker", extra]
        return CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(
                    name,
                    f"---\nname: {name}\ndescription: Exact canonical text\n---\n"
                    + "Résumé 🦉\r\n" * (10000 if name == extra else 1),
                )
                for name in names
            ),
            ("panels", "panels-worker"),
        )

    first, second = snapshot("old-extra"), snapshot("new-extra")
    duplicate = CanonicalSkillSnapshot(
        (*first.sources, CanonicalSkillSource("PANELS", first.sources[0].source_text)),
        first.required_skill_names,
    )
    surrogate = replace(
        first, sources=(replace(first.sources[0], source_text="🦉a\ud800"), *first.sources[1:])
    )
    surrogate_range = replace(
        first, sources=(replace(first.sources[0], source_text="🦉\ud800\ud801"), *first.sources[1:])
    )
    unsafe_name = replace(
        first, sources=(replace(first.sources[0], skill_name="panels\n"), *first.sources[1:])
    )
    snapshots = [first, first, second, duplicate, surrogate, surrogate_range, unsafe_name]
    expected: list[Any] = []
    py, ts = tmp_path / "py", tmp_path / "ts"
    for root in (py, ts):
        _prepare(root)
    paths = ExecutorPaths(py / "state")
    for value in snapshots[:3]:
        generation = install_native_skill_snapshot(value, paths=paths, native_home=py / "provider")
        expected.append(
            [str(generation.relative_to(py)), _tree(paths.root, py), _tree(py / "provider", py)]
        )
    generation = install_native_skill_snapshot(second, paths=paths, native_home=py / "provider")
    (generation / "skills" / "panels" / "SKILL.md").write_text("corrupt", encoding="utf-8")
    with pytest.raises(Exception) as corrupt_error:
        install_native_skill_snapshot(second, paths=paths, native_home=py / "provider")
    assert type(corrupt_error.value).__name__ == "BackendSpawnFailed"
    for value in snapshots[3:]:
        with pytest.raises(Exception) as error:
            install_native_skill_snapshot(value, paths=paths, native_home=py / "provider")
        assert type(error.value).__name__ == "BackendSpawnFailed"
        expected.append(str(error.value))
    (ts / "input.json").write_text(
        json.dumps({"snapshots": [json.dumps(asdict(s)) for s in snapshots]}),
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key not in homes
    }
    environment.update(homes)
    environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
    node_started = time.perf_counter()
    process = subprocess.Popen(
        ["node", "server/executor/mac/testing/skills_parity.ts", str(ts)],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=40)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)
        raise
    node_seconds = time.perf_counter() - node_started
    assert process.returncode == 0, stdout + stderr
    result = json.loads((ts / "result.json").read_text())
    assert result["report"] == expected
    assert result["mac_only"]["folder_swap"]["refused"] is True
    assert result["mac_only"]["folder_swap"]["unchanged"] is True
    assert result["mac_only"]["corrupt_rebuilt"] is True
    assert result["mac_only"]["corrupt_preserved"] is True
    assert [case["case"] for case in result["mac_only"]["refusals"]] == [
        "managed-directory",
        "managed-file",
        "foreign-link",
        "case-only-name",
        "absent-home",
        "relative-home",
        "linked-home",
        "linked-skills",
        "linked-state-ancestor",
        "link-loop",
    ]
    for case in result["mac_only"]["refusals"]:
        assert case["refused"] and case["unchanged"] and case["entry_named"], case
    assert result["lifecycle"] == {
        "stopped_refused": True,
        "expired": True,
        "rejected": True,
        "faults": [],
        "blocked": [".install.lock"],
    }
    elapsed = time.perf_counter() - started
    request.node.user_properties.extend(
        [
            ("elapsed_seconds", round(elapsed, 3)),
        ]
    )
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-22-mac-skills.json").write_text(
            json.dumps(
                {
                    "equal": True,
                    "reference": "fresh branch Python on synthetic populated state",
                    "elapsed_seconds": elapsed,
                    "node_seconds": node_seconds,
                    "observations": result["report"],
                    "lifecycle": result["lifecycle"],
                    "accepted_mac_differences": result["mac_only"],
                    "python_corrupt_refusal": str(corrupt_error.value),
                    "limits": "No network, provider, native Mac, or account coexistence proof.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
