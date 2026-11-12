"""Scratch parity on private databases and the existing temporary Git fixture.

No function receives this checkout, this Ticket's roots, or a live database.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes
from tests.unit.test_ticket_scratch import DONE, LIVE, NOW, _git, _layout, _worktree

from planner.environments import ticket_scratch as scratch

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


def _database(root: Path, states: dict[str, tuple[str, int]]) -> Path:
    path = root / "states.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tickets(id TEXT, stage TEXT, updated_at INTEGER)")
        connection.executemany(
            "INSERT INTO tickets VALUES(?,?,?)",
            [(identity, *state) for identity, state in states.items()],
        )
    return path


def _value(root: Path, states: dict[str, tuple[str, int]], database: Path) -> dict[str, Any]:
    return {
        "root": str(root),
        "home": str(root / "home"),
        "database": str(database),
        "now": NOW,
        "states": list(states.items()),
    }


def _normalize(value: Any, root: Path) -> Any:
    if isinstance(value, str):
        return value.replace(str(root), "<root>")
    if isinstance(value, list):
        return [_normalize(item, root) for item in value]
    if isinstance(value, dict):
        return {_normalize(key, root): _normalize(item, root) for key, item in value.items()}
    return value


def test_scratch_database_gate_and_ticket_rules_match_python(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    states = {
        "t_aaaaaaaa": DONE,
        "t_bbbbbbbb": LIVE,
        "t_cccccccc": ("done", NOW - 599),
        "t_dddddddd": ("done", NOW - 601),
    }
    database = _database(tmp_path, states)
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(database)
    names = ["t_aaaaaaaa", "t_aaaaaaaa-build", "prefix-t_aaaaaaaa", "t_aaaaaaaaa", "t_iiiiiiii"]
    branches = [None, "ticket/t_aaaaaaaa-other", "prefix/t_aaaaaaaaa", "feature/work"]
    gates: list[dict[str, Any]] = [
        {"root": str(tmp_path), "database": str(database), "db_path": str(alias)},
        {"root": str(tmp_path), "database": str(database), "db_path": str(tmp_path / "other.db")},
        {"root": str(tmp_path), "database": None, "db_path": str(database)},
        {"root": None, "database": str(database), "db_path": str(database)},
    ]
    layout = scratch.ScratchLayout.under(tmp_path, home=tmp_path / "home")
    expected = {
        "layout": {"repository": str(layout.repository), "folders": list(map(str, layout.folders))},
        "gates": [
            str(found.repository)
            if (
                found := scratch.layout_for(
                    Path(gate["root"]) if gate["root"] else None, gate["database"], gate["db_path"]
                )
            )
            else None
            for gate in gates
        ],
        "states": list(scratch.read_ticket_states_from(str(database)).items()),
        "names": [scratch.ticket_id_of_name(name) for name in names],
        "branches": [scratch.ticket_id_of_branch(branch) for branch in branches],
        "finished": [
            (identity, scratch.finished(identity, states, NOW))
            for identity in [*states, "t_zzzzzzzz"]
        ],
    }
    actual = _node(
        tmp_path,
        "probe_scratch_rules",
        {
            **_value(tmp_path, states, database),
            "names": names,
            "branches": branches,
            "gates": gates,
        },
    )
    assert actual == json.loads(json.dumps(expected))
    _report(request, "unit-21-scratch-rules.json", started, actual)


def _git_fixture(root: Path) -> tuple[scratch.ScratchLayout, dict[str, Any]]:
    root.mkdir()
    layout = _layout(root)
    repository = layout.repository
    (repository / "tracked.txt").write_text("published\n")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "published fixture")
    _git(repository, "push", "-q", "origin", "HEAD:staging")
    _git(repository, "fetch", "-q", "origin")
    task, _, trees, pct, evidence = layout.folders
    clean = _worktree(layout, trees / "t_aaaaaaaa", "ticket/t_aaaaaaaa")
    for entry in (task / "t_aaaaaaaa-build", pct / "t_aaaaaaaa", evidence / "t_aaaaaaaa"):
        entry.mkdir()
    (task / "t_aaaaaaaa.log").write_text("log")
    (task / "notes.log").write_text("not a Ticket")
    dirty = _worktree(layout, trees / "t_cccccccc", "ticket/t_cccccccc")
    (dirty / "draft.py").write_text("unsaved\n")
    _git(dirty, "config", "status.showUntrackedFiles", "no")
    unpushed = _worktree(layout, trees / "t_dddddddd", "feature/retained-work")
    _git(unpushed, "commit", "-qm", "local only", "--allow-empty")
    nested = _worktree(layout, task / "t_eeeeeeee" / "source", "ticket/t_ffffffff")
    clone = task / "t_hhhhhhhh"
    subprocess.run(["git", "clone", "-q", str(repository), str(clone)], check=True)
    _git(clone, "checkout", "-qb", "off-head")
    _git(
        clone,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "local off-head",
        "--allow-empty",
    )
    _git(clone, "checkout", "-q", "-")
    submodule = _worktree(layout, trees / "t_jjjjjjjj", "ticket/t_jjjjjjjj")
    _git(
        submodule,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(root / "origin.git"),
        "module",
    )
    _git(submodule, "commit", "-qm", "published submodule")
    _git(submodule, "push", "-q", "origin", "HEAD:submodule-fixture")
    _git(submodule, "config", "submodule.module.ignore", "all")
    (submodule / "module/tracked.txt").write_text("unsaved submodule\n")
    external = root / "outside-kept"
    external.mkdir()
    (external / "value").write_text("keep\n")
    link = task / "t_kkkkkkkk"
    link.symlink_to(external, target_is_directory=True)
    outside = _worktree(layout, root / "elsewhere/t_mmmmmmmm", "ticket/t_mmmmmmmm")
    published = _worktree(layout, trees / "t_pppppppp", "ticket/t_pppppppp")
    _git(published, "commit", "-qm", "published unmerged", "--allow-empty")
    _git(published, "push", "-q", "origin", "HEAD:published-unmerged")
    fresh = _worktree(layout, trees / "t_rrrrrrrr", "ticket/t_rrrrrrrr")
    recent = task / "t_gggggggg"
    recent.mkdir()
    states = {
        "t_aaaaaaaa": DONE,
        "t_cccccccc": DONE,
        "t_dddddddd": DONE,
        "t_ffffffff": LIVE,
        "t_hhhhhhhh": DONE,
        "t_jjjjjjjj": DONE,
        "t_gggggggg": ("done", NOW - 1),
    }
    database = _database(root, states)
    value = {
        **_value(root, states, database),
        "layout": {"repository": str(layout.repository), "folders": list(map(str, layout.folders))},
        "late_dirty": str(fresh / "after-inventory.txt"),
        "later_clean": [str(dirty / "draft.py"), str(fresh / "after-inventory.txt")],
        "watch_paths": list(
            map(
                str,
                [
                    clean,
                    dirty,
                    unpushed,
                    nested.parent,
                    clone,
                    submodule,
                    link,
                    external / "value",
                    outside,
                    published,
                    fresh,
                    recent,
                    task / "notes.log",
                ],
            )
        ),
    }
    return layout, value


def _python_git(layout: scratch.ScratchLayout, value: dict[str, Any]) -> dict[str, Any]:
    states = {identity: (str(state[0]), int(state[1])) for identity, state in value["states"]}
    inventory = scratch.collect_ticket_scratch(layout, states, NOW)
    Path(value["late_dirty"]).write_text("changed after inventory\n")
    applied = scratch.remove_ticket_scratch(layout, inventory, states, NOW)
    for path in value["later_clean"]:
        Path(path).unlink()
    already_kept = {item["path"] for item in applied.skipped}
    remembered = scratch.clean_finished_ticket_scratch(layout, value["database"], NOW, already_kept)
    daily = scratch.remove_ticket_scratch(
        layout,
        scratch.collect_ticket_scratch(layout, states, NOW),
        states,
        NOW,
    )
    remaining = {}
    for name in value["watch_paths"]:
        path = Path(name)
        remaining[name] = (
            "link"
            if path.is_symlink()
            else "directory"
            if path.is_dir()
            else "file"
            if path.exists()
            else None
        )
    return {
        "inventory": [item.as_dict() for item in inventory],
        "applied": applied.as_dict(),
        "remembered": remembered.as_dict(),
        "daily": daily.as_dict(),
        "already_kept": sorted(already_kept),
        "remaining": remaining,
        "branches": _git(
            layout.repository, "for-each-ref", "--format=%(refname:short)", "refs/heads"
        ).splitlines(),
    }


def test_scratch_git_work_and_fresh_apply_checks_match_python(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    python_root, node_root = tmp_path / "python", tmp_path / "node"
    layout, value = _git_fixture(python_root)
    expected = _normalize(_python_git(layout, value), python_root)
    _, node_value = _git_fixture(node_root)
    env = _environment(node_root)
    env.update({"GIT_DIR": str(node_root / "ambient-poison"), "GIT_WORK_TREE": str(node_root)})
    actual = _normalize(_node(node_root, "probe_scratch_git", node_value, env), node_root)
    assert actual == json.loads(json.dumps(expected))
    _report(request, "unit-21-scratch-git.json", started, actual)


def test_scratch_runtime_waits_and_fences_held_pass_at_deadline(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    layout = _layout(tmp_path)
    entry = _worktree(layout, layout.folders[2] / "t_aaaaaaaa", "ticket/t_aaaaaaaa")
    database = _database(tmp_path, {"t_aaaaaaaa": DONE})
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
            **_value(tmp_path, {"t_aaaaaaaa": DONE}, database),
            "layout": {
                "repository": str(layout.repository),
                "folders": list(map(str, layout.folders)),
            },
            "git_marker": str(marker),
            "watch_paths": [str(entry)],
        },
        env,
    )
    assert actual["before_first"] == 0 and actual["pending_timers"] == 0
    assert actual["seen"] == [[], ["kept-on-first-pass"]]
    assert 15 <= actual["stop_ms"] < 500
    assert actual["after_stop"] == actual["disabled_timers"] == 0
    assert set(actual["timer_values"]) == {scratch.CLEANUP_INTERVAL_SECONDS * 1000}
    assert actual["owned_git_reaped"] and actual["entry_preserved"]
    _report(request, "unit-21-scratch-runtime.json", started, actual)
