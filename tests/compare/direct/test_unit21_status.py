"""Synthetic host status, maintenance rechecks, and genuine backup evidence parity.

Reuse the existing backup fixtures, no server/browser host and no real probes.
OS read-error text differs between bindings, so evidence records explicit IO failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_database_backups import _legacy_snapshot, _seed_database, _seed_managed_tree

from planner.core.config import load_config
from planner.environments import backup, vps_status

ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "server/environments/vps_status/testing/compare.ts"
NOW = "2026-11-05T12:00:00+00:00"
EPOCH = datetime.fromisoformat(NOW).timestamp()


def _seed(root: Path) -> Path:
    for directory in ("runtime", "backups", "logs", "app", "external"):
        (root / directory).mkdir(parents=True)
    source = root / "runtime/database.sqlite"
    _seed_database(source)
    _seed_managed_tree(source.parent, "synthetic")
    (source.parent / "skills" / "inner-link").symlink_to("panels", target_is_directory=True)
    (root / "external/keep").write_text("external retained")
    return source


def _snapshot(
    source: Path, root: Path, name: str, created: Any = "2026-11-05T10:00:00+00:00"
) -> Path:
    path = _legacy_snapshot(source, root / "backups" / name)
    metadata = json.loads((path / "metadata.json").read_text())
    if created is None:
        metadata.pop("created_at")
    else:
        metadata["created_at"] = created
    (path / "metadata.json").write_text(json.dumps(metadata))
    return path


def _tree(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[key] = {"link": os.readlink(path)}
        elif path.is_dir():
            result[key] = {"directory": True}
        else:
            result[key] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return result


def _io(work: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"result": work(), "error": None}
    except UnicodeError:
        return {"result": None, "error": "decode"}
    except OSError:
        return {"result": None, "error": "io"}
    except RuntimeError as error:
        return {"result": None, "error": str(error)}


def _resolve_path(path: str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def _python(case: dict[str, Any]) -> Any:
    root = Path(case["root"])
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(root / "runtime/database.sqlite"),
            "PLAN_BACKUP_DIR": str(root / "backups"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_APP_SHA": "a" * 40,
        },
    )
    policy_values = dict(case.get("policy", {}))
    for name in ("backup_warning_age", "backup_critical_age", "temporary_expiry"):
        if name in policy_values:
            policy_values[name] = timedelta(seconds=policy_values[name])
    policy = replace(vps_status.DEFAULT_VPS_STATUS_POLICY, **policy_values)
    disk = case.get(
        "disk",
        {"f_frsize": 4096, "f_blocks": 100_000_000, "f_bavail": 60_000_000},
    )
    dependencies = vps_status.VpsStatusDependencies(
        now=lambda: datetime.fromisoformat(case["now"]),
        platform_name=lambda: case.get("platform", "Darwin"),
        statvfs=lambda _: type("SyntheticDisk", (), disk)(),
        process_lines=lambda: case.get("processes", []),
        git_worktree_output=lambda _: case.get("git", ""),
        live_reference_paths=lambda: map(Path, case.get("references", [])),
    )

    def collect() -> dict[str, object]:
        return vps_status.collect_vps_status(
            config, application_root=root / "app", dependencies=dependencies, policy=policy
        ).as_dict()

    original_hash = backup._sha256

    def digest(path: Path) -> str:
        if str(path) == case.get("denied_database"):
            raise PermissionError("synthetic denied backup read")
        return original_hash(path)

    with patch.object(backup, "_sha256", digest):
        if case["operation"] == "status":
            return collect()
        if case["operation"] == "cleanup":
            inventory = vps_status.collect_cleanup_inventory(
                config, dependencies=dependencies, policy=policy
            )
            for mutation in case.get("mutations", []):
                path = Path(mutation["path"])
                if mutation["kind"] == "touch":
                    os.utime(path, (mutation["time"], mutation["time"]))
                elif mutation["kind"] == "symlink":
                    shutil.rmtree(path)
                    path.symlink_to(mutation["target"], target_is_directory=True)
                elif mutation["kind"] == "remove":
                    shutil.rmtree(path)
                else:
                    path.write_text(mutation.get("text", ""))
            return {
                "inventory": inventory.as_dict(),
                "applied": vps_status.apply_cleanup_inventory(inventory).as_dict(),
                "tree": _tree(root),
            }
        paths = sorted((root / "backups").glob("snapshot-*"))
        return {
            "resolved_paths": [_io(partial(_resolve_path, path)) for path in case.get("paths", [])],
            "evidence": {
                path.name: _io(partial(backup.is_verified_snapshot, path)) for path in paths
            },
            "snapshots": _io(lambda: list(map(str, backup.verified_snapshots(root / "backups")))),
            "retention": _io(
                lambda: list(map(str, backup.verified_snapshot_retention_plan(root / "backups")))
            ),
            "backup": vps_status._backup_section(
                root / "backups", datetime.fromisoformat(case["now"]), policy
            ),
            "collection": _io(collect),
        }


def _node(root: Path, cases: list[dict[str, Any]]) -> Any:
    root.mkdir(parents=True, exist_ok=True)
    request = root / "request.json"
    request.write_text(json.dumps(cases))
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PLAN_")
        and name not in {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    }
    for name in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME", "PLAN_HERMES_HOME"):
        path = root / name.lower()
        path.mkdir(exist_ok=True)
        env[name] = str(path)
    config = root / "config.yaml"
    config.write_text("{}\n")
    env.update(
        {
            "PLAN_CONFIG_PATH": str(config),
            "PLAN_DB_PATH": str(root / "synthetic.sqlite"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_BACKUP_DIR": str(root / "backups"),
            "PLAN_WORKSPACE_ROOT": str(root),
            "PLAN_TICKET_SCRATCH_ROOT": str(root),
            "PLAN_TICKET_SCRATCH_DATABASE": str(root / "synthetic.sqlite"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_APP_ROOT": str(root),
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "key"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
        }
    )
    script = (
        "import {readFileSync} from 'node:fs';"
        "const m=await import(process.argv[1]);"
        "process.stdout.write(JSON.stringify(await m.probe_status("
        "JSON.parse(readFileSync(process.argv[2],'utf8')))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, COMPANION.as_uri(), str(request)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _compare(
    tmp_path: Path,
    templates: list[tuple[Path, dict[str, Any]]],
    name: str,
    request: pytest.FixtureRequest,
) -> list[Any]:
    started = time.perf_counter()
    python_cases: list[dict[str, Any]] = []
    ts_cases: list[dict[str, Any]] = []
    for index, (template, spec) in enumerate(templates):
        for side, cases in (("python", python_cases), ("typescript", ts_cases)):
            root = tmp_path / f"{side}-{index}"
            shutil.copytree(template, root, symlinks=True)
            encoded = json.dumps({"root": str(template), "now": NOW, **spec})
            cases.append(json.loads(encoded.replace(str(template), str(root))))
    (tmp_path / "python-homes").mkdir()
    homes = create_synthetic_homes(tmp_path / "python-homes")
    with synthetic_home_environment(homes):
        expected = [_python(case) for case in python_cases]
    actual = _node(tmp_path / "node", ts_cases)

    def normal(value: Any) -> Any:
        encoded = json.dumps(value, sort_keys=True)
        for index, (py, ts) in enumerate(zip(python_cases, ts_cases, strict=True)):
            encoded = encoded.replace(py["root"], f"<root-{index}>")
            encoded = encoded.replace(ts["root"], f"<root-{index}>")
        return json.loads(encoded)

    assert normal(actual) == normal(expected)
    report = {
        "case": name,
        "seconds": time.perf_counter() - started,
        "differences": [],
        "scenarios": len(templates),
        "answers": normal(actual),
        "normalization": (
            "Synthetic root paths only; OS read and UTF-8 decode errors retain explicit "
            "io/decode failures instead of binding-specific text."
        ),
    }
    report_root = request.config.getoption("--compare-report-dir")
    if report_root:
        target = Path(report_root)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"unit-21.status-{name}.json").write_text(json.dumps(report, indent=2))
    return cast(list[Any], normal(actual))


def test_unit21_status_populated_and_sanitized(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    root = tmp_path / "template"
    source = _seed(root)
    current = backup.create_database_backup(source, root / "backups", "current-python-fixture")
    current.rename(root / "backups/snapshot-current")
    metadata_path = root / "backups/snapshot-current/metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["created_at"] = "2026-11-05T10:00:00+00:00"
    metadata_path.write_text(json.dumps(metadata))
    (root / "logs/big.log").write_text("l" * 100)
    (root / "logs/small.log").write_text("small")
    (root / "logs/link.log").symlink_to("../external/keep")
    temporary = root / "backups/.backup-old"
    temporary.mkdir()
    os.utime(temporary, (EPOCH - 90000, EPOCH - 90000))
    marker = "secret-only-in-argv-and-worktree"
    worktree = root / marker
    worktree.mkdir()
    processes = (
        [
            f"123 1 00:01 /synthetic/python3 -m planner serve --token {marker}",
            "124 1 01:02 /synthetic/panels serve",
            "125 1 00:02 node server/main.ts",
            "126 1 00:01 panels serve",
        ]
        + ["1 0 age unrelated"] * 125
        + ["999 0 00:03 panels serve"]
    )
    status_case = {
        "operation": "status",
        "platform": "Linux",
        "policy": {"log_warning_bytes": 64},
        "processes": processes,
        "git": f"worktree {worktree}\nbranch refs/heads/secret\n\nworktree {root / 'absent'}\n",
    }
    answers = _compare(tmp_path, [(root, status_case)], "populated", request)
    assert marker not in json.dumps(answers)
    assert len(answers[0]["workloads"]["items"]) == 3
    assert answers[0]["backup"]["verified_snapshot_count"] == 1


def test_unit21_cleanup_rechecks_changed_targets(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    root = tmp_path / "template"
    source = _seed(root)
    for index in range(4):
        _snapshot(source, root, f"snapshot-{index}", f"2026-11-0{index + 1}T00:00:00+00:00")
    for name in ("safe", "replaced", "live", "freshened"):
        path = root / "backups" / f".backup-{name}"
        path.mkdir()
        os.utime(path, (EPOCH - 90000, EPOCH - 90000))
    for name in ("rotate.log", "blocked.log"):
        (root / "logs" / name).write_text("l" * 100)
    mutations = [
        {
            "kind": "symlink",
            "path": str(root / "backups/.backup-replaced"),
            "target": "../external",
        },
        {"kind": "touch", "path": str(root / "backups/.backup-freshened"), "time": EPOCH},
        {"kind": "write", "path": str(root / "logs/blocked.log.1"), "text": "retained"},
        {
            "kind": "write",
            "path": str(root / "backups/snapshot-0/database.sqlite"),
            "text": "corrupt after inventory",
        },
    ]
    cleanup_case = {
        "operation": "cleanup",
        "policy": {"log_warning_bytes": 64},
        "references": [str(root / "backups/.backup-live")],
        "mutations": mutations,
    }
    answers = _compare(tmp_path, [(root, cleanup_case)], "cleanup", request)
    assert len(answers[0]["applied"]["removed"]) == 1
    assert len(answers[0]["applied"]["rotated"]) == 1
    assert len(answers[0]["applied"]["review_needed"]) == 5
    assert answers[0]["tree"]["external/keep"]


def test_unit21_backup_evidence_order_links_and_io(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    sorting = tmp_path / "sorting"
    source = _seed(sorting)
    for name, created in (
        ("snapshot-missing", None),
        ("snapshot-earlier-raw", "2026-11-05T00:00:00-12:00"),
        ("snapshot-later-raw", "2026-11-05T01:00:00+14:00"),
        ("snapshot-malformed", "zz-malformed"),
    ):
        _snapshot(source, sorting, name, created)
    (sorting / "real/child").mkdir(parents=True)
    (sorting / "alias").symlink_to("real/child", target_is_directory=True)
    path_vectors = [
        str(sorting / "alias") + "/../database.sqlite",
        str(sorting / "alias") + "/missing/../file",
        f"~{pwd.getpwuid(os.getuid()).pw_name}/panels-unit21-nonexistent-child",
        f"~{pwd.getpwuid(0).pw_name}/panels-unit21-nonexistent-child",
        f"~panels_unit21_missing_{os.getpid()}/example",
    ]
    future = tmp_path / "future"
    _snapshot(_seed(future), future, "snapshot-future", "2027-01-01T00:00:00+00:00")
    corrupt = tmp_path / "corrupt"
    source = _seed(corrupt)
    _snapshot(source, corrupt, "snapshot-inner-symlink")
    bad = _snapshot(source, corrupt, "snapshot-extra-file")
    (bad / "files/skills/extra").write_text("not in manifest")
    bad = _snapshot(source, corrupt, "snapshot-bad-manifest")
    (bad / "files/manifest.json").write_text("corrupt")
    bad = _snapshot(source, corrupt, "snapshot-db-link")
    (bad / "database.sqlite").unlink()
    (bad / "database.sqlite").symlink_to("../snapshot-inner-symlink/database.sqlite")
    bad = _snapshot(source, corrupt, "snapshot-metadata-link")
    (bad / "metadata.json").unlink()
    (bad / "metadata.json").symlink_to("../snapshot-inner-symlink/metadata.json")
    (corrupt / "backups/snapshot-root-link").symlink_to(
        "snapshot-inner-symlink", target_is_directory=True
    )
    denied = tmp_path / "denied"
    target = _snapshot(_seed(denied), denied, "snapshot-denied")
    encoded = tmp_path / "encoded"
    bad = _snapshot(_seed(encoded), encoded, "snapshot-invalid-utf8")
    metadata_path = bad / "metadata.json"
    metadata_path.write_bytes(
        metadata_path.read_bytes().replace(b"legacy-fixture", b"legacy-\xfffixture")
    )
    answers = _compare(
        tmp_path,
        [
            (sorting, {"operation": "evidence", "paths": path_vectors}),
            (future, {"operation": "evidence"}),
            (corrupt, {"operation": "evidence"}),
            (denied, {"operation": "evidence", "denied_database": str(target / "database.sqlite")}),
            (encoded, {"operation": "evidence"}),
        ],
        "evidence",
        request,
    )
    assert answers[0]["resolved_paths"][0]["result"].endswith("/real/database.sqlite")
    assert answers[0]["resolved_paths"][2]["result"] == (
        pwd.getpwuid(os.getuid()).pw_dir + "/panels-unit21-nonexistent-child"
    )
    assert answers[0]["resolved_paths"][4]["error"] == "Could not determine home directory."
    assert answers[0]["snapshots"]["result"][0].endswith("snapshot-malformed")
    assert answers[0]["snapshots"]["result"][1].endswith("snapshot-later-raw")
    assert answers[0]["backup"]["state"] == answers[1]["backup"]["state"] == "review_needed"
    assert answers[2]["backup"]["verified_snapshot_count"] == 1
    assert answers[3]["backup"]["state"] == "unavailable"
    assert answers[3]["collection"]["error"] == "io"
    assert answers[4]["backup"]["state"] == "unavailable"
    assert answers[4]["collection"]["error"] == "decode"
