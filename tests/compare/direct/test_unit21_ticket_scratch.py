"""Scratch parity on private databases and the existing temporary Git fixture.

No function receives this checkout, this Ticket's roots, or a live database.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, TypedDict

import pytest
from tests.support.synthetic_homes import create_synthetic_homes
from tests.support.ticket_workspace_git import _git, _layout

from planner.environments import ticket_scratch as scratch_source
from planner.ticket_workspaces import filesystem as workspaces
from planner.ticket_workspaces.contracts import OWNERSHIP_FILE, RETENTION_SECONDS, WorkspaceState

NOW = RETENTION_SECONDS + 100


def _guard(root: Path, path: Path) -> Path:
    root = root.resolve(strict=True)
    if not path.resolve().is_relative_to(root):
        raise ValueError("outside synthetic root")
    return path


def _worktree(layout: Any, path: Path, branch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(layout.repository, "worktree", "add", "-qb", branch, str(path))
    return path


def _database(root: Path, states: Any) -> Path:
    database = root / "states.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE ticket_workspace_lifecycle"
            "(ticket_id TEXT,ended_at INTEGER,deleted INTEGER)"
        )
        conn.executemany(
            "INSERT INTO ticket_workspace_lifecycle VALUES(?,?,?)",
            [(s.ticket_id, s.ended_at, s.deleted) for s in states.values()],
        )
    return database


def _value(root: Path, states: Any, database: Path) -> dict[str, Any]:
    return {
        "root": str(root),
        "home": str(root / "home"),
        "database": str(database),
        "now": NOW,
        "states": [(key, asdict(value)) for key, value in states.items()],
    }


ROOT = Path(__file__).resolve().parents[3]
COMPANION = ROOT / "server/environments/ticket_scratch/testing/compare.ts"


def _environment(root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "GIT_"))
        and key not in {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    }
    env.update(create_synthetic_homes(root))
    for key, name in {
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
        "PLAN_WORKSPACE_ROOT": "workspace",
        "PLAN_TICKET_SCRATCH_ROOT": "scratch",
        "PLAN_APP_ROOT": "app",
    }.items():
        (root / name).mkdir(exist_ok=True)
        env[key] = str(root / name)
    config = root / "config.yaml"
    config.write_text("{}\n")
    env.update(
        {
            "PLAN_CONFIG_PATH": str(config),
            "PLAN_DB_PATH": str(root / "states.sqlite"),
            "PLAN_TICKET_SCRATCH_DATABASE": str(root / "states.sqlite"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "key"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
        }
    )
    return env


def _node(
    root: Path, function: str, value: dict[str, Any], env: dict[str, str] | None = None
) -> Any:
    request = root / "request.json"
    request.write_text(json.dumps(value))
    script = (
        "import {readFileSync} from 'node:fs';const m=await import(process.argv[1]);"
        "const v=JSON.parse(readFileSync(process.argv[2],'utf8'));"
        f"process.stdout.write(JSON.stringify(await m.{function}(v)));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, COMPANION.as_uri(), str(request)],
        cwd=ROOT,
        env=env or _environment(root),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _report(request: pytest.FixtureRequest, name: str, started: float, result: Any) -> None:
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / name).write_text(
            json.dumps(
                {
                    "complete": True,
                    "seconds": time.perf_counter() - started,
                    "result": result,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )


def _normalize(value: Any, root: Path) -> Any:
    if isinstance(value, str):
        return re.sub(
            r"(t_[0-9a-z]{8})-[0-9a-f]{32}-[0-9a-f]{32}",
            r"\1-<workspace>-<retirement>",
            value.replace(str(root), "<root>"),
        )
    if isinstance(value, list):
        return [_normalize(item, root) for item in value]
    if isinstance(value, dict):
        return {_normalize(key, root): _normalize(item, root) for key, item in value.items()}
    return value


class LayoutGate(TypedDict):
    root: str | None
    database: str
    db_path: str


def test_scratch_database_gate_and_ticket_rules_match_python(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    states = {
        "t_aaaaaaaa": WorkspaceState("t_aaaaaaaa", 0, False),
        "t_bbbbbbbb": WorkspaceState("t_bbbbbbbb", None, False),
        "t_cccccccc": WorkspaceState("t_cccccccc", 100, True),
    }
    database = _database(tmp_path, states)
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(database)
    gates: list[LayoutGate] = [
        {"root": str(tmp_path), "database": str(database), "db_path": str(alias)},
        {"root": str(tmp_path), "database": str(database), "db_path": str(tmp_path / "other.db")},
        {"root": None, "database": str(database), "db_path": str(database)},
    ]
    layout = scratch_source.ScratchLayout.under(tmp_path, home=tmp_path / "home")
    expected = {
        "layout": {"repository": str(layout.repository), "folders": list(map(str, layout.folders))},
        "gates": [
            str(found.repository)
            if (
                found := scratch_source.layout_for(
                    Path(g["root"]) if g["root"] else None, g["database"], g["db_path"]
                )
            )
            else None
            for g in gates
        ],
        "states": [(key, asdict(value)) for key, value in states.items()],
    }
    actual = _node(
        tmp_path, "probe_scratch_rules", {**_value(tmp_path, states, database), "gates": gates}
    )
    assert actual == json.loads(json.dumps(expected))
    _report(request, "unit-21-scratch-rules.json", started, actual)


def _git_fixture(root: Path) -> dict[str, Any]:
    root.mkdir()
    layout = _layout(root)
    tickets = root / "files/tickets"
    states = {}
    paths = {}
    for letter, kind in zip(
        "abcdefghjkmnp",
        [
            "clean",
            "dirty",
            "unpublished",
            "active",
            "recent",
            "unknown",
            "invalid",
            "no-remote",
            "late-dirty",
            "published",
            "off-head",
            "submodule",
            "mixed",
        ],
        strict=True,
    ):
        ticket = "t_" + letter * 8
        folder = _guard(root, tickets / ticket)
        if kind == "off-head":
            # Legacy linked worktrees still exist. Their recorded Ticket branch
            # must be preserved even when HEAD is detached at a published commit.
            branch = f"ticket/{ticket}"
            checkout = _worktree(layout, folder / "worktree", branch)
            workspaces.workspace_identity(folder)
            marker_path = folder / OWNERSHIP_FILE
            marker = json.loads(marker_path.read_text())
            marker.update(repository=str(layout.repository), worktree=str(checkout), branch=branch)
            marker_path.write_text(json.dumps(marker))
            assert (checkout / ".git").is_file()
        else:
            checkout = workspaces.prepare_workspace(folder, layout.repository, ticket)
            assert (checkout / ".git").is_dir()
        paths[kind] = str(folder)
        if kind != "unknown":
            states[ticket] = WorkspaceState(
                ticket, None if kind == "active" else NOW - 1 if kind == "recent" else 0, False
            )
        if kind == "mixed":
            _worktree(layout, checkout / "nested/t_dddddddd", "ticket/t_dddddddd-nested")
        if kind == "submodule":
            _git(
                checkout,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-q",
                str(root / "origin.git"),
                "module",
            )
            _git(checkout, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "submodule")
            _git(checkout, "push", "-q", "origin", "HEAD:published-submodule")
            _git(checkout, "config", "submodule.module.ignore", "all")
        if kind == "dirty":
            (checkout / "draft.txt").write_text("unsaved")
        if kind in {"unpublished", "published", "off-head"}:
            _git(checkout, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", kind)
            if kind == "published":
                _git(checkout, "push", "-q", "origin", "HEAD:published-unmerged")
            if kind == "off-head":
                _git(checkout, "checkout", "--detach", "HEAD~1")
        if kind == "invalid":
            if (checkout / ".git").is_dir():
                (checkout / ".git").rename(checkout / ".invalid-git-metadata")
            (checkout / ".git").write_text("gitdir: missing")
        if kind == "no-remote":
            # Independent clone configuration cannot mutate the Project's origin.
            _git(checkout, "remote", "remove", "origin")
        if kind == "recent":
            (folder / "artifacts/report.txt").write_text("retain for seven days")
    database = _database(root, states)
    return {**_value(root, states, database), "tickets": str(tickets), "paths": paths}


def _python_git(value: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Any:
    root = Path(value["root"])
    states = {key: WorkspaceState(**item) for key, item in value["states"]}
    tickets = _guard(root, Path(value["tickets"]))
    from planner.conversation import worker_scratch

    monkeypatch.setattr(worker_scratch, "_scratch_root", lambda: root / "home/.pw")

    def before(path: Path) -> None:
        _guard(root, path)
        if str(path) == value["paths"]["late-dirty"]:
            (path / "worktree/late.txt").write_text("fresh change")

    result = workspaces.cleanup_local_workspaces(tickets, states, NOW, before_remove=before)
    manifest = json.loads((tickets.parent / "ticket-workspaces-cleanup.json").read_text())
    return {
        "result": asdict(result),
        "manifest": manifest,
        "remaining": {kind: Path(path).exists() for kind, path in value["paths"].items()},
    }


def test_scratch_git_work_and_fresh_apply_checks_match_python(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = time.perf_counter()
    python_root, node_root = tmp_path / "python", tmp_path / "node"
    # The guard runs before any Git or cleanup call and rejects real roots and symlink escapes.
    with pytest.raises(ValueError):
        _guard(tmp_path, Path("/home/vps/Deployments/Panels"))
    (tmp_path / "escape").symlink_to("/home/vps/Deployments/Panels")
    with pytest.raises(ValueError):
        _guard(tmp_path, tmp_path / "escape/ticket")
    expected = _normalize(_python_git(_git_fixture(python_root), monkeypatch), python_root)
    actual = _normalize(_node(node_root, "probe_scratch_git", _git_fixture(node_root)), node_root)
    assert actual == expected
    assert actual["remaining"] == {
        "clean": False,
        "dirty": True,
        "unpublished": True,
        "active": True,
        "recent": True,
        "unknown": True,
        "invalid": True,
        "no-remote": True,
        "late-dirty": False,  # Detached after admission, then kept in retirement storage.
        "published": False,
        "off-head": True,
        "submodule": True,
        "mixed": True,
    }
    # Two old folders and the recent checkout retire; the recent artifact stays.
    assert len(actual["result"]["removed"]) == 3
    recent = node_root / "files/tickets/t_eeeeeeee"
    assert not (recent / "worktree").exists()
    assert (recent / "artifacts/report.txt").read_text() == "retain for seven days"
    retained_late_work = list((node_root / "files/.ticket-retired").glob("*/worktree/late.txt"))
    assert len(retained_late_work) == 1
    assert retained_late_work[0].read_text() == "fresh change"
    _report(request, "unit-21-scratch-git.json", started, actual)


def test_scratch_runtime_waits_and_fences_held_pass_at_deadline(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:

    started = time.perf_counter()
    layout = _layout(tmp_path)
    entry = _worktree(layout, layout.folders[2] / "t_aaaaaaaa", "ticket/t_aaaaaaaa")
    database = _database(tmp_path, {"t_aaaaaaaa": WorkspaceState("t_aaaaaaaa", 0, False)})
    env = _environment(tmp_path)
    executable = shutil.which("git", path=env["PATH"])
    assert executable is not None
    commands = tmp_path / "commands"
    commands.mkdir()
    marker, gate = tmp_path / "git-started", tmp_path / "git-gate"
    os.mkfifo(gate)
    wrapper = commands / "git"
    wrapper.write_text(
        "#!/bin/sh\ncase \"$*\" in *'status --porcelain'*)\n"
        f"printf '%s' \"$$\" > {shlex.quote(str(marker))}\n"
        f"IFS= read -r unused < {shlex.quote(str(gate))}\n;;\nesac\n"
        f'exec {shlex.quote(executable)} "$@"\n'
    )
    wrapper.chmod(0o700)
    env["PATH"] = str(commands) + os.pathsep + env["PATH"]
    actual = _node(
        tmp_path,
        "probe_scratch_runtime",
        {
            **_value(tmp_path, {"t_aaaaaaaa": WorkspaceState("t_aaaaaaaa", 0, False)}, database),
            "layout": {
                "repository": str(layout.repository),
                "folders": list(map(str, layout.folders)),
            },
            "git_marker": str(marker),
            "watch_paths": [str(entry)],
        },
        env,
    )
    assert actual["before_first"] == 1 and actual["pending_timers"] == 0
    assert actual["actual_retained"]
    assert actual["seen"] == [[], []]
    assert actual["after_stop"] == actual["disabled_timers"] == 0
    assert actual["timer_values"]
    assert all(
        scratch_source.CLEANUP_INTERVAL_SECONDS * 1000 - 1000 <= delay
        <= scratch_source.CLEANUP_INTERVAL_SECONDS * 1000
        for delay in actual["timer_values"]
    )
    assert actual["owned_git_reaped"] and actual["entry_preserved"]
    _report(request, "unit-21-scratch-runtime.json", started, actual)


def test_repository_selection_matches_python_on_populated_project_children(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    # Gap: scratch cleanup proofs never select a Project repository. Reuse their
    # populated local Git fixture to prove selection and inspection refusals.
    started = time.perf_counter()
    layout = _layout(tmp_path)
    repository = _guard(tmp_path, layout.repository)
    (repository / "README.md").write_text("populated repository selection proof\n")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "populate selection fixture")
    unique = _guard(tmp_path, tmp_path / "unique")
    unique.mkdir()
    _git(unique, "clone", "-q", str(repository), "headroom")
    malformed = _guard(tmp_path, tmp_path / "malformed")
    (malformed / "headroom").mkdir(parents=True)
    (malformed / "headroom/.git").write_text("invalid Git metadata\n")
    multiple = _guard(tmp_path, tmp_path / "multiple")
    multiple.mkdir()
    _git(multiple, "clone", "-q", str(repository), "headroom")
    (multiple / "other/.git").mkdir(parents=True)
    empty = _guard(tmp_path, tmp_path / "empty")
    empty.mkdir()
    paths = {
        "unique": unique,
        "malformed": malformed,
        "multiple": multiple,
        "direct": repository,
        "conventional": repository.parent,
        "empty": empty,
    }
    expected: dict[str, Any] = {}
    for name, path in paths.items():
        try:
            selected = workspaces.repository_root(_guard(tmp_path, path))
            expected[name] = {"repository": str(selected) if selected else None}
        except RuntimeError as error:
            expected[name] = {"error": str(error)}
    expected["null"] = {"repository": None}
    assert expected["unique"] == {"repository": str(unique / "headroom")}
    assert expected["malformed"] == {
        "error": f"Cannot inspect Project repository: {malformed / 'headroom'}"
    }
    assert "error" in expected["multiple"]
    assert (unique / "headroom/README.md").read_text() == "populated repository selection proof\n"
    actual = _node(
        tmp_path,
        "probe_repository_selection",
        {"root": str(tmp_path), "paths": {name: str(path) for name, path in paths.items()}},
    )
    assert actual == expected
    _report(request, "unit21-repository-selection.json", started, actual)
