"""Update parity through real stores/routes and explicit scripted external operations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import threading
import time
import zipfile
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes
from tests.support.update_apps import release

from planner.core import change_signal
from planner.environments import update_attempts
from planner.environments.update_attempts import (
    FileUpdateAttemptStore,
    attempt_to_dict,
    parse_attempt,
)
from planner.environments.update_contracts import UpdateAttempt

ROOT = Path(__file__).resolve().parents[3]
NOW = "2026-11-05T12:00:00+00:00"
COMPANION = ROOT / "server/environments/updates/testing/compare.ts"


def _attempt(identity: str = "attempt-one") -> dict[str, Any]:
    return {
        "attempt_id": identity,
        "from_sha": "a" * 40,
        "to_sha": "b" * 40,
        "mode": "idle",
        "confirm_activity": False,
        "state": "waiting",
        "created_at": NOW,
        "updated_at": NOW,
        "release": json.loads(json.dumps(asdict(release()))),
        "detail": None,
        "deadline_at": None,
        "backup": None,
        "steps": {"pressed": NOW},
        "mac_attempt_id": None,
    }


def _environment(root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_")
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
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    config = root / "config.yaml"
    config.write_text("{}\n")
    env.update(
        {
            "PLAN_CONFIG_PATH": str(config),
            "PLAN_DB_PATH": str(root / "database.sqlite"),
            "PLAN_TICKET_SCRATCH_DATABASE": str(root / "database.sqlite"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_SERVER_LIFECYCLE_LEASE_PATH": str(root / "server-lifecycle.lock"),
            "PLAN_SERVER_URL": "http://127.0.0.1:0",
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "key"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
        }
    )
    return env


def _node(root: Path, value: dict[str, Any] | list[dict[str, Any]], function: str) -> Any:
    path = root / "request.json"
    path.write_text(json.dumps(value))
    script = (
        "import {readFileSync} from 'node:fs'; "
        "const module=await import(process.argv[1]); "
        "const codec=await import(process.argv[3]); "
        "const input=codec.parse_attempt_json(readFileSync(process.argv[2],'utf8')); "
        f"process.stdout.write(codec.canonical_json(await module.{function}(input)));"
    )
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            COMPANION.as_uri(),
            str(path),
            (COMPANION.parent.parent / "codec.ts").as_uri(),
        ],
        cwd=ROOT,
        env=_environment(root),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _python_store(path: Path, operations: list[dict[str, Any]]) -> dict[str, Any]:
    emitted: list[None] = []
    previous = change_signal.emit
    previous_now = update_attempts.utc_now
    change_signal.emit = lambda: emitted.append(None)
    update_attempts.utc_now = lambda: NOW
    try:
        store = FileUpdateAttemptStore(path)
        answers: list[dict[str, Any]] = []
        for operation in operations:
            try:
                result = None
                if operation["kind"] == "read":
                    current = store.read()
                    result = None if current is None else attempt_to_dict(current)
                elif operation["kind"] == "create":
                    result = attempt_to_dict(store.create(parse_attempt(operation["attempt"])))
                elif operation["kind"] == "import_legacy":
                    adopted = update_attempts.import_legacy_request(
                        store, Path(operation["data_directory"]), operation["running_sha"]
                    )
                    result = None if adopted is None else attempt_to_dict(adopted)
                elif operation["kind"] == "change":

                    def change(
                        current: UpdateAttempt, patch: dict[str, Any] = operation["patch"]
                    ) -> UpdateAttempt:
                        return replace(current, **patch)

                    result = attempt_to_dict(
                        store.change(
                            operation["attempt_id"],
                            frozenset(operation["expected"]),
                            change,
                        )
                    )
                else:
                    store.remove(operation["attempt_id"], frozenset(operation["expected"]))
                answers.append({"result": result, "error": None})
            except Exception as error:
                answers.append(
                    {"result": None, "error": {"type": type(error).__name__, "message": str(error)}}
                )
        return {
            "answers": answers,
            "emitted": len(emitted),
            "file_text": path.read_text() if path.exists() else None,
            "file_mode": path.stat().st_mode & 0o777 if path.exists() else None,
        }
    finally:
        change_signal.emit = previous
        update_attempts.utc_now = previous_now


def _report(request: pytest.FixtureRequest, name: str, result: dict[str, Any]) -> None:
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / name).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")


def _compare_store(
    root: Path, paths: tuple[Path, Path], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    expected = _python_store(paths[0], operations)
    actual: dict[str, Any] = _node(
        root, {"path": str(paths[1]), "now": NOW, "operations": operations}, "probe_store"
    )
    # Compare canonical bytes too: NaN equality and float/int equality hide metadata loss.
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
    return actual


def test_attempt_store_preserves_versions_and_compare_and_swap(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    initial = _attempt()
    initial["future"] = {"message": "雪", "10": "ten", "2": "two", "\ue000": "bmp", "😀": "astral"}
    numeric_edges = {
        "float": 1.0,
        "negative_zero": -0.0,
        "small": 1e-7,
        "integer": 9007199254740993123456789,
        "nan": float("nan"),
        "infinity": float("inf"),
        "negative_infinity": float("-inf"),
        "literal": 'NaN Infinity -Infinity "NaN" \\ Infinity',
        "escaped_marker": "__panels_update_constant__0",
    }
    initial["future"].update(numeric_edges)
    initial["release"]["future_release"] = {"nested": {"keep": True}}
    initial["release"]["future_release"]["nested"].update(numeric_edges)
    initial["release"]["manifest"]["future_manifest"] = ["é", numeric_edges]
    initial["release"]["mac"] = {
        "artifact_id": 1.0,
        "archive_sha256": "f" * 64,
        "archive_size": 9007199254740993123456789,
    }
    for index, field in enumerate(("publication_run_id", "artifact_id", "proof_artifact_id")):
        initial["release"][field] = 9007199254740993123456789 + index
    operations: list[dict[str, Any]] = [
        {"kind": "read"},
        {
            "kind": "change",
            "attempt_id": "wrong",
            "expected": ["waiting"],
            "patch": {"detail": "no"},
        },
        {
            "kind": "change",
            "attempt_id": "attempt-one",
            "expected": ["waiting"],
            "patch": {"detail": "Réessayer", "steps": {"pressed": NOW, "checked": NOW}},
        },
        {"kind": "create", "attempt": _attempt("duplicate")},
    ]
    python_path = tmp_path / "python-attempt.json"
    node_path = tmp_path / "node-attempt.json"
    for path in (python_path, node_path):
        path.write_text(
            json.dumps(initial).replace(
                '"__panels_update_constant__0"',
                r'"\u005f\u005fpanels_update_constant__0"',
            )
        )
    paths = (python_path, node_path)
    actual = _compare_store(tmp_path, paths, operations)
    fresh = {**_attempt("fresh"), "future": numeric_edges}
    more: list[dict[str, Any]] = [
        {
            "kind": "change",
            "attempt_id": "attempt-one",
            "expected": ["waiting"],
            "patch": {"state": "failed"},
        },
        {"kind": "create", "attempt": fresh},
        {"kind": "remove", "attempt_id": "fresh", "expected": ["installing"]},
        {"kind": "remove", "attempt_id": "fresh", "expected": ["waiting"]},
        {"kind": "read"},
    ]
    actual_more = _compare_store(tmp_path, paths, more)
    for path in (python_path, node_path):
        path.write_text("{broken")
    recovery: list[dict[str, Any]] = [
        {"kind": "read"},
        {"kind": "create", "attempt": _attempt("recovered")},
        {"kind": "read"},
    ]
    actual_recovery = _compare_store(tmp_path, paths, recovery)
    encoding_results = []
    for encoding in ("invalid_utf8", "utf8_bom"):
        unreadable = _attempt("encoding-recovery")
        unreadable["release"]["future_release"] = {"value": "__invalid_byte__"}
        encoded = json.dumps(unreadable).encode("utf8")
        encoded = (
            encoded.replace(b"__invalid_byte__", b"\xff")
            if encoding == "invalid_utf8"
            else b"\xef\xbb\xbf" + encoded
        )
        for path in (python_path, node_path):
            path.write_bytes(encoded)
        recover_encoding: list[dict[str, Any]] = [
            {"kind": "read"},
            {"kind": "create", "attempt": _attempt("encoding-recovery")},
            {"kind": "read"},
        ]
        actual_encoding = _compare_store(tmp_path, paths, recover_encoding)
        encoding_results.append({"encoding": encoding, "result": actual_encoding})
    # A previous server's request must reactivate its Mac without replacing any
    # existing attempt file, even an unreadable one. Reuse this store proof host.
    legacy_results: list[dict[str, Any]] = []
    request_payload: dict[str, Any] = {
        "request_id": 42,
        "release": _attempt()["release"],
        "state": "applied",
        "mode": "legacy-unknown",
        "confirm_activity": ["confirmed"],
        "created_at": {},
    }
    running_sha = request_payload["release"]["manifest"]["app_sha"]
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy_request = legacy_root / "owner-update-request.json"
    legacy_lifecycle = legacy_root / "deployment-lifecycle.json"
    for existing, sha, state in (
        ("{broken", running_sha, "applied"),
        (None, None, "applied"),
        (None, "wrong-sha", "applied"),
        (None, running_sha, "pending"),
        (None, running_sha, "applied"),
        (None, running_sha, "applying"),
    ):
        request_payload["state"] = state
        legacy_lifecycle.write_text(
            "{broken"
            if state == "applying"
            else json.dumps({"requested_sha": running_sha, "prior_sha": "c" * 40})
        )
        legacy_request.write_text(json.dumps(request_payload))
        for path in (python_path, node_path):
            path.unlink(missing_ok=True)
            if existing is not None:
                path.write_text(existing)
        adoption = [
            {"kind": "import_legacy", "data_directory": str(legacy_root), "running_sha": sha}
        ]
        legacy_results.append(_compare_store(tmp_path, paths, adoption))
    _report(
        request,
        "unit-21-attempt-store.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "first": actual,
            "after_settlement": actual_more,
            "corrupt_record_recovery": actual_recovery,
            "unreadable_encoding_recovery": encoding_results,
            "legacy_adoption": legacy_results,
        },
    )


def test_attempt_store_waits_for_python_lock_and_rechecks_state(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    path = tmp_path / "attempt.json"
    store = FileUpdateAttemptStore(path)
    store.create(parse_attempt(_attempt()))
    entered = threading.Event()
    release_lock = threading.Event()
    errors: list[BaseException] = []

    def change_in_python() -> None:
        def held(current: Any) -> Any:
            entered.set()
            assert release_lock.wait(10)
            return replace(current, state="preparing", detail="Python won")

        try:
            store.change("attempt-one", frozenset({"waiting"}), held)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=change_in_python)
    worker.start()
    assert entered.wait(5)
    input_path = tmp_path / "contended.json"
    input_path.write_text(
        json.dumps(
            {"path": str(path), "now": NOW, "attempt_id": "attempt-one", "expected": ["waiting"]}
        )
    )
    script = (
        "import {readFileSync} from 'node:fs'; "
        "const {probe_contended_change}=await import(process.argv[1]); "
        "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
        "process.stdout.write('ready\\n'); "
        "process.stdout.write(JSON.stringify(await probe_contended_change(input)));"
    )
    child = subprocess.Popen(
        ["node", "--input-type=module", "-e", script, COMPANION.as_uri(), str(input_path)],
        cwd=ROOT,
        env=_environment(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        time.sleep(0.05)
        release_lock.set()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        actual = json.loads(stdout)
        assert actual["error"] == {
            "type": "UpdateConflict",
            "message": "The update changed or already moved on.",
        }
        assert actual["synchronous_ms"] >= 40
        assert actual["timer_delay_ms"] >= 40
        assert not errors
        final = store.read()
        assert final is not None and (final.state, final.detail) == ("preparing", "Python won")
        _report(
            request,
            "unit-21-attempt-lock.json",
            {
                "complete": True,
                **actual,
                "scope": (
                    "Actual Python FileUpdateAttemptStore holds flock "
                    "across a controlled 50 ms callback. "
                    "Node must wait, re-read, and refuse stale waiting CAS."
                ),
            },
        )
    finally:
        release_lock.set()
        worker.join(timeout=10)
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)


def test_update_experience_and_microsecond_boundaries_match_python(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    from planner.environments.logic.update_experience import (
        deployment_status,
        instant,
        resolve,
        settle,
    )

    started = time.perf_counter()
    base = _attempt()
    ready = base["release"]
    newer = deepcopy(ready)
    newer["manifest"]["app_sha"] = "c" * 40
    cases: list[dict[str, Any]] = []

    def case(
        state: str | None,
        *,
        now: str = NOW,
        offered: Any = ready,
        running: str = "b" * 40,
        phase: str | None = None,
        mac: bool = True,
        report: str | None = None,
        **changes: Any,
    ) -> None:
        attempt = None if state is None else {**deepcopy(base), "state": state, **changes}
        cases.append(
            {
                "attempt": attempt,
                "available": offered,
                "app_sha": running,
                "now": now,
                "mac_applies": mac,
                "mac_phase": phase,
                "mac_reported_at": report,
                "mac_report_seconds": 90,
                "success_seconds": 300,
            }
        )

    case(None, offered=None)
    for timestamp in ("2026-11-05T12:15:00+00:00", "2026-11-05T12:15:00.000001+00:00"):
        case("installing", now=timestamp, deadline_at="2026-11-05T12:15:00+00:00")
    case(
        "installing",
        now="2026-11-05T12:15:00.000001+00:00",
        running="a" * 40,
        deadline_at="2026-11-05T12:15:00+00:00",
    )
    case("failed", detail="no", offered=None)
    case("failed", detail="no", offered=newer)
    case("done", mac=False)
    case("done", phase="current", offered=newer)
    case("done", phase="ready_to_restart", now="2026-11-05T12:01:29+00:00")
    case("done", now="2026-11-05T12:01:30+00:00")
    case("done", now="2026-11-05T12:01:30.000001+00:00")
    case("done", phase="failed")
    case("done", phase="reverted")
    case("done", report="2026-11-05T12:01:40+00:00", now="2026-11-05T12:02:30+00:00")
    case("done", now="2026-11-05T12:04:59.999999+00:00", mac=False)
    case("done", now="2026-11-05T12:05:00+00:00", mac=False)
    case("failed", detail="no", now="2026-11-05T12:04:59.999999+00:00")
    case("failed", detail="no", now="2026-11-05T12:05:00+00:00")
    case("done", updated_at="2026-11-05T14:30:00.123456+02:30", now="2026-11-05T12:01:30.123456Z")
    case("done", updated_at="1969-12-31T23:59:59.999999+00:00", now="1970-01-01T00:00:01+00:00")
    case("done", updated_at="2026-11-05T12:00:00", now=NOW)
    expected: list[dict[str, Any]] = []
    for item in cases:
        record = None if item["attempt"] is None else parse_attempt(item["attempt"])
        offered = (
            None if item["available"] is None else update_attempts.parse_release(item["available"])
        )
        try:
            now = instant(item["now"])
            ended = None if record is None else settle(record, item["app_sha"], now)
            expected.append(
                {
                    "resolve": list(
                        resolve(
                            record,
                            offered,
                            item["app_sha"],
                            mac_applies=item["mac_applies"],
                            mac_phase=item["mac_phase"],
                            mac_reported_at=None
                            if item["mac_reported_at"] is None
                            else instant(item["mac_reported_at"]),
                            mac_report_seconds=item["mac_report_seconds"],
                            now=now,
                        )
                    ),
                    "settle": None if ended is None else attempt_to_dict(ended),
                    "deployment": deployment_status(
                        record, item["app_sha"], success_seconds=item["success_seconds"], now=now
                    ),
                }
            )
        except ValueError as error:
            expected.append({"error": str(error)})
    # JSON round trip turns Python action tuples into the same JSON arrays as TS.
    expected = json.loads(json.dumps(expected))
    actual = _node(tmp_path, cases, "probe_experience")
    assert actual == expected
    _report(
        request,
        "unit-21-update-experience.json",
        {
            "complete": True,
            "cases": len(cases),
            "seconds": time.perf_counter() - started,
            "answers": actual,
        },
    )


def _assert_route_callers(conn: sqlite3.Connection, callers: dict[str, str]) -> None:
    assert tuple(
        conn.execute(
            "SELECT EXISTS(SELECT 1 FROM managers WHERE id=?), "
            "EXISTS(SELECT 1 FROM tickets WHERE id=?)",
            (callers["manager"], callers["ticket"]),
        ).fetchone()
    ) == (1, 1)


def _seed_route_callers(conn: sqlite3.Connection) -> dict[str, str]:
    from datetime import datetime

    from planner.core.clock import TestClock as RouteClock
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.managers import data as managers
    from planner.tickets import data as tickets

    clock = RouteClock(datetime.fromisoformat(NOW))
    manager = managers.create_manager(
        conn, title="Route caller Manager", project_id="project_other", clock=clock
    )
    ticket = tickets.create_ticket(
        conn,
        title="Route caller Ticket",
        principal=OWNER_PRINCIPAL,
        now=clock.now_unix(),
        title_max_chars=200,
        worker_type="coding",
        manager_id=manager.id,
    )
    callers = {"manager": manager.id, "ticket": ticket.id}
    _assert_route_callers(conn, callers)
    return callers


def _service_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seed_callers: bool = False,
    node: bool = False,
) -> tuple[Any, dict[str, Any]]:
    from tests.unit.test_update_service import Harness

    from planner.environments import update_service

    host: Any = Harness(tmp_path / "python", node=node)
    callers: dict[str, str] = {}
    if seed_callers:
        from contextlib import closing

        from planner.core.db import connect

        with closing(connect(str(host.database))) as connection:
            callers = _seed_route_callers(connection)
    node_root = tmp_path / "node"
    node_database = node_root / "data" / "planner.db"
    node_database.parent.mkdir(parents=True)
    with sqlite3.connect(host.database) as source, sqlite3.connect(node_database) as target:
        source.backup(target)
        if seed_callers:
            _assert_route_callers(target, callers)
    from datetime import datetime

    epoch = datetime.fromisoformat(NOW).timestamp()
    host.service._now = lambda: epoch
    host.service._last_checked = epoch
    host.service._mac_configured = lambda: True
    monkeypatch.setattr(update_service, "utc_now", lambda: NOW)
    monkeypatch.setattr(update_attempts, "utc_now", lambda: NOW)
    return host, {
        "root": str(node_root),
        "database_path": str(node_database),
        "now": NOW,
        "release": json.loads(json.dumps(asdict(host.release))),
        "callers": callers,
    }


def _identities(value: Any) -> Any:
    """UUIDs are intentionally fresh on each implementation's owner press."""
    attempts: dict[str, str] = {}
    macs: dict[str, str] = {}

    def visit(item: Any) -> Any:
        if isinstance(item, list):
            return [visit(child) for child in item]
        if not isinstance(item, dict):
            return item
        result = {}
        for key, child in item.items():
            if key in {"attempt_id", "mac_attempt_id"} and child is not None:
                table = attempts if key == "attempt_id" else macs
                result[key] = table.setdefault(child, f"{key}-{len(table) + 1}")
            else:
                result[key] = visit(child)
        return result

    return visit(json.loads(json.dumps(value)))


def test_service_owner_actions_and_synchronous_poll_match_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    host, value = _service_fixture(tmp_path, monkeypatch)
    actions: list[dict[str, Any]] = [
        {"op": "status"},
        {"op": "activity", "value": "busy"},
        {"op": "install", "mode": "now"},
        {"op": "install", "mode": "idle"},
        {"op": "poll"},
        {"op": "cancel"},
        {"op": "activity", "value": "idle"},
        {"op": "install", "mode": "now"},
        {"op": "activity", "value": "busy"},
        {"op": "advance"},
        {"op": "status"},
        {"op": "install", "mode": "idle"},
        {"op": "now"},
        {"op": "now", "confirm_activity": True},
        {"op": "advance"},
        {"op": "poll"},
        {"op": "change", "patch": {"deadline_at": "2000-01-01T00:00:00+00:00"}},
        {"op": "poll"},
        {"op": "status"},
        {"op": "activity", "value": "idle"},
        {"op": "retry"},
        {"op": "launch_error", "value": "no user manager"},
        {"op": "advance"},
        {"op": "status"},
        {"op": "launch_error", "value": None},
        {"op": "retry"},
        {"op": "advance"},
        {"op": "change", "patch": {"state": "done"}},
        {"op": "retry_mac"},
        {"op": "status"},
    ]
    answers: list[dict[str, Any]] = []
    for action in actions:
        try:
            result = None
            current = host.store.read()
            match action["op"]:
                case "activity":
                    host.system.activity = action["value"]
                case "launch_error":
                    host.launch_error = (
                        None if action["value"] is None else OSError(action["value"])
                    )
                case "status":
                    result = asdict(host.service.status())
                case "poll":
                    result = host.service.poll()
                case "install":
                    result = attempt_to_dict(
                        host.service.install(
                            host.release.manifest.app_sha,
                            action["mode"],
                            confirm_activity=action.get("confirm_activity", False),
                        )
                    )
                case "cancel":
                    host.service.cancel(current.attempt_id)
                case "now":
                    result = attempt_to_dict(
                        host.service.update_now(
                            current.attempt_id,
                            confirm_activity=action.get("confirm_activity", False),
                        )
                    )
                case "retry":
                    result = attempt_to_dict(host.service.retry(current.attempt_id))
                case "retry_mac":
                    result = attempt_to_dict(host.service.retry_mac(current.attempt_id))
                case "advance":
                    host.advance()
                case "change":
                    host.store.change(
                        current.attempt_id,
                        frozenset({current.state}),
                        lambda current, patch=action["patch"]: replace(current, **patch),
                    )
            answers.append({"result": result, "error": None})
        except Exception as error:
            answers.append(
                {"result": None, "error": {"type": type(error).__name__, "message": str(error)}}
            )
    expected = _identities(
        {
            "answers": answers,
            "closed": host.system.workspace_admission.closed,
            "launches": len(host.launched),
            "attempt": asdict(host.store.read()),
        }
    )
    actual = _identities(_node(tmp_path, {**value, "actions": actions}, "probe_service"))
    assert actual == expected
    node_started = time.perf_counter()
    node_host, node_value = _service_fixture(tmp_path / "node-frontier", monkeypatch, node=True)
    from planner.environments import update_service
    from planner.environments.update_install import (
        migration_graph,
        require_exact_schema_frontier,
        write_installer,
    )

    staged = node_host.root / (".app-next-" + node_host.release.manifest.app_sha)
    with sqlite3.connect(node_host.database) as db:
        original = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    versions = staged / "src/planner/core/migrations/versions"
    (versions / "data-only.py").write_text(
        f'revision = "unit21_data_only"\ndown_revision = "{original}"\n'
        'def upgrade():\n    op.execute("UPDATE tickets SET recap=recap")\n'
    )
    graph = migration_graph(staged)
    heads = sorted(set(graph) - {parent for parents in graph.values() for parent in parents})
    node_value["schema_heads"] = heads
    # Harness eagerly prefetches. Reset only its cache to model cold v2 admission.
    node_host.service._ready = None
    trace: list[str] = []
    exact = require_exact_schema_frontier
    stage = node_host.service._stage

    def admitted(database: Path, candidate: Path) -> None:
        trace.append("frontier")
        exact(database, candidate)

    def stage_again(*args: Any) -> Path:
        trace.append("stage")
        return Path(stage(*args))

    def frontier(revision: str) -> None:
        with sqlite3.connect(node_host.database) as db:
            db.execute("DELETE FROM alembic_version")
            db.execute("INSERT INTO alembic_version VALUES (?)", (revision,))

    with monkeypatch.context() as scoped:
        scoped.setattr(update_service, "require_exact_schema_frontier", admitted)
        scoped.setattr(node_host.service, "_stage", stage_again)
        scoped.setattr(
            node_host.service, "_prepare_mac_update", lambda _release: trace.append("prepare_mac")
        )
        frontier_error = None
        try:
            node_host.service.prefetch()
        except ValueError as failure:
            frontier_error = str(failure)
        cold = dict(
            error=frontier_error,
            ready=node_host.service.ready_release() is not None,
            mac=trace.count("prepare_mac"),
        )
        frontier(heads[0])
        node_host.service.prefetch()
        ready = node_host.service.ready_release() is not None
        node_host.service.install(node_host.release.manifest.app_sha, "now", confirm_activity=False)
        frontier(original)
        node_host.advance()
        failed = node_host.store.read()
        denied = dict(
            state=failed.state,
            detail=failed.detail,
            backup=failed.backup,
            launches=len(node_host.launched),
            reopened=not node_host.system.workspace_admission.closed,
        )
        frontier(heads[0])
        # Trace only external prepare/launch, while retaining the real source installer setup.
        prepare = write_installer

        def prepare_again(path: Path) -> Path:
            trace.append("prepare")
            return prepare(path)

        scoped.setattr(update_service, "write_installer", prepare_again)
        launch = node_host.service._launch

        def launch_again(*args: Any) -> None:
            trace.append("launch")
            launch(*args)

        scoped.setattr(node_host.service, "_launch", launch_again)
        node_host.service.retry(failed.attempt_id)
        node_host.advance()
        expected_node = dict(
            cold=cold,
            ready=ready,
            denied=denied,
            final_state=node_host.store.read().state,
            backups=int(node_host.store.read().backup is not None),
            launches=len(node_host.launched),
            trace=trace,
        )
    actual_node = _node(tmp_path / "node-frontier", node_value, "probe_node_frontier")
    assert actual_node == expected_node
    assert cold["error"] and not cold["ready"] and cold["mac"] == 0
    assert (
        denied["state"] == "failed"
        and denied["backup"] is None
        and denied["launches"] == 0
        and denied["reopened"]
    )
    assert (
        actual_node["final_state"] == "installing"
        and actual_node["launches"] == 1
        and actual_node["backups"] == 0
    )
    _report(
        request,
        "unit-21-update-service.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "result": actual,
            "node_frontier": actual_node,
            "node_frontier_extra_seconds": time.perf_counter() - node_started,
        },
    )


def test_service_continuations_fence_stop_and_observe_external_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    started = time.perf_counter()
    _, value = _service_fixture(tmp_path, monkeypatch)
    installed = tmp_path / "installed"
    (installed / "app").mkdir(parents=True)
    (installed / "data").mkdir()
    installed_database = installed / "data" / "planner.db"
    installed_database.touch()
    app_alias = tmp_path / "app-alias"
    app_alias.symlink_to(installed / "app", target_is_directory=True)
    database_alias = tmp_path / "database-alias"
    database_alias.symlink_to(installed_database)
    false_root = tmp_path / "false-installed"
    (false_root / "app").mkdir(parents=True)
    (false_root / "data").mkdir()
    (false_root / "data" / "planner.db").symlink_to(installed_database)
    value["installed"] = [
        {
            "app_sha": "a" * 40,
            "app_root": str(app_alias),
            "database": str(database_alias),
            "service_unit": "own.service",
        },
        {
            "app_sha": "a" * 40,
            "app_root": str(false_root / "app"),
            "database": str(false_root / "data" / "planner.db"),
            "service_unit": "own.service",
        },
        {
            "app_sha": None,
            "app_root": str(app_alias),
            "database": str(database_alias),
            "service_unit": "own.service",
        },
        {
            "app_sha": "a" * 40,
            "app_root": str(app_alias),
            "database": str(database_alias),
            "service_unit": None,
        },
    ]
    actual = _node(tmp_path, value, "probe_continuations")
    assert actual["installed_roots"] == [str(installed.resolve()), None, None, None]
    assert actual["old_finally_preserves_new_flight"]
    assert 15 <= actual["stage_stop_ms"] < 500
    assert 15 <= actual["backup_stop_ms"] < 500
    assert actual["stage_flight"] == {
        "provider": 2,
        "stage": 2,
        "backup": 0,
        "prepare": 0,
        "launch": 0,
        "ready": None,
        "trace": ["stage", "stage"],
    }
    assert actual["backup_fence"] == {
        "provider": 1,
        "stage": 1,
        "backup": 1,
        "prepare": 0,
        "launch": 0,
        "state": "preparing",
        "closed": True,
        "poll": False,
        "unchanged_after_ownership": True,
    }
    launch = actual["launch_fence"]
    assert 15 <= launch["elapsed_ms"] < 500
    assert {key: value for key, value in launch.items() if key != "elapsed_ms"} == {
        "launches": 1,
        "closed": True,
        "state": "installing",
        "unchanged_after_ownership": True,
    }
    assert actual["audit_refusal"]["state"] == "failed"
    assert actual["audit_refusal"]["detail"].startswith("The Ticket integrity check refused")
    assert actual["audit_refusal"]["global_registry_unchanged"]
    assert not actual["audit_refusal"]["closed"] and actual["audit_refusal"]["launches"] == 0
    assert actual["commit_refusal"]["state"] == "failed"
    assert "scripted commit step failure" in actual["commit_refusal"]["detail"]
    assert not actual["commit_refusal"]["closed"] and actual["commit_refusal"]["launches"] == 0
    assert actual["commit_refusal"]["poll"] is False
    assert actual["external_observation"] == {"seen": True, "state": "failed"}
    assert actual["readiness_poll"] == {
        "before_poll": {"state": "waiting", "launches": 0},
        "holds_worker_pass": True,
        "state": "installing",
        "launches": 1,
    }
    _report(
        request,
        "unit-21-update-continuations.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "result": actual,
            "boundary": (
                "Real database and admission barrier with scripted stage/backup/launch. "
                "File observation emits changes; readiness poll advances waiting work. "
                "Held stage, backup, and installer refusal settle after the absolute stop "
                "deadline and database ownership ends, without later publication or writes. "
                "Real provider and production adapter shutdown remain open."
            ),
        },
    )


def test_owner_routes_populated_mac_reads_and_deployment_snapshot_match_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from planner.core.errors import PlannerError
    from planner.environments.update_api import router
    from planner.executor.update_contracts import MacUpdateStatus

    started = time.perf_counter()
    host, value = _service_fixture(tmp_path, monkeypatch, seed_callers=True)
    host.system.activity = "busy"
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.updates = host.service
    counts = {"health_reads": 0, "mac_reads": 0}
    status = MacUpdateStatus(
        "mac-controller", "c" * 40, "ready_to_restart", "controller status read"
    )

    class Endpoint:
        def health_payload(self) -> dict[str, str]:
            counts["health_reads"] += 1
            return {"presence": "present", "platform": "darwin"}

    class Mac:
        def status(self) -> MacUpdateStatus:
            counts["mac_reads"] += 1
            return status

    app.state.executor_endpoint = Endpoint()
    app.state.mac_updates = Mac()

    @app.exception_handler(PlannerError)
    async def planner_error(_request: Any, error: Any) -> JSONResponse:
        return JSONResponse(error.to_payload(), status_code=400)

    def door(method: str, path: str = "", payload: Any = None, **extra: Any) -> dict[str, Any]:
        item = {"method": method, "path": "/api/updates" + path, **extra}
        if payload is not None:
            item["payload"] = payload
        return item

    operations = [
        door("GET"),
        door("POST", "/install", {"app_sha": "b" * 40, "mode": "idle"}),
        door("GET"),
        door("POST", "/attempts/@attempt/now", {}),
        door("POST", "/attempts/@attempt/cancel"),
        door("POST", "/install", {"app_sha": "b" * 40, "mode": "bad"}),
        door("POST", "/install", {"app_sha": "b" * 40, "mode": "idle"}),
        door("POST", "/attempts/@attempt/now", {"confirm_activity": True}),
        door("POST", "/attempts/@attempt/retry", seed={"state": "failed"}),
        door("POST", "/attempts/@attempt/retry-mac", seed={"state": "done"}),
        door("GET"),
    ]
    protected_requests = [
        ("GET", "/api/updates", None),
        ("POST", "/api/updates/install", {"app_sha": "b" * 40, "mode": "idle"}),
        ("POST", "/api/updates/attempts/@attempt/cancel", None),
        ("POST", "/api/updates/attempts/@attempt/now", {}),
        ("POST", "/api/updates/attempts/@attempt/retry", None),
        ("POST", "/api/updates/attempts/@attempt/retry-mac", None),
    ]
    denied = [
        (
            method,
            path,
            payload,
            {"x-plan-actor": "worker", "x-plan-ticket-id": value["callers"]["ticket"]},
        )
        for method, path, payload in protected_requests
    ]
    denied.extend(
        ("GET", "/api/updates", None, headers)
        for headers in [
            {"x-plan-actor": "chief"},
            {"x-plan-actor": "manager", "x-plan-manager-id": value["callers"]["manager"]},
        ]
    )
    for method, path, payload, headers in denied:
        item = {"method": method, "path": path, "headers": headers}
        if payload is not None:
            item["payload"] = payload
        operations.append(item)
    expected = []
    with TestClient(app) as client:
        for operation in operations:
            attempt = host.store.read()
            if "seed" in operation:
                host.store.change(
                    attempt.attempt_id,
                    frozenset({attempt.state}),
                    lambda current, patch=operation["seed"]: replace(current, **patch),
                )
            path = operation["path"].replace(
                "@attempt", attempt.attempt_id if attempt else "missing"
            )
            before = host.store.path.read_text() if operation.get("headers") else None
            response = client.request(
                operation["method"],
                path,
                json=operation.get("payload"),
                headers=operation.get("headers"),
            )
            if before is not None:
                assert host.store.path.read_text() == before
            expected.append({"status": response.status_code, "body": response.json()})
    actual = _node(
        tmp_path,
        {**value, "requests": operations, "mac_status": asdict(status)},
        "probe_update_routes",
    )
    assert _identities(actual["responses"]) == _identities(expected)
    assert actual["nonowner_unchanged"]
    assert {key: actual[key] for key in counts} == counts
    assert actual["resource"]["captures"] == 1
    assert actual["resource"]["body"] == actual["resource"]["after"]
    assert json.loads(actual["resource"]["revision"]) == actual["resource"]["body"]
    _report(
        request,
        "unit-21-update-routes.json",
        {
            "complete": True,
            "callers": value["callers"],
            "seconds": time.perf_counter() - started,
            "result": _identities(actual),
            "boundary": (
                "Existing createApp/Fastify inject and FastAPI TestClient, real attempt stores, "
                "explicit controller read callbacks and persisted Manager/Ticket callers in "
                "the copied application database. Background advancement stays paused in both "
                "HTTP/status fixtures; separate service/continuation proofs exercise it. "
                "No listener or browser."
            ),
        },
    )


def test_provider_trust_evidence_and_streamed_download_match_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    import httpx

    from planner.environments.update_provider import GitHubUpdateProvider

    started = time.perf_counter()
    root = tmp_path / "provider"
    root.mkdir()
    token = root / "token"
    token.write_text("unit21-dummy-credential\n")
    published = _attempt()["release"]
    published["mac"] = {"artifact_id": 9, "archive_sha256": "f" * 64, "archive_size": 10}
    run = {
        "id": 1,
        "head_branch": "main",
        "event": "push",
        "conclusion": "success",
        "head_repository": {"full_name": "owner/repository"},
        "path": ".github/workflows/deploy.yml",
        "head_sha": "b" * 40,
        "created_at": NOW,
    }
    artifacts = [
        {"name": "panels-publication-proof", "id": 3, "expired": False},
        {"name": "panels-app", "id": 2, "expired": False},
        {"name": "panels-mac-runtime", "id": 9, "expired": False},
    ]
    cases: list[dict[str, Any]] = []
    proof_archives: dict[str, Path] = {}

    def transport_case(name: str, action: str, responses: dict[str, Any], **extra: Any) -> None:
        cases.append(
            {
                "name": name,
                "action": action,
                "run_id": 1,
                "artifact_id": 2,
                "responses": responses,
                **extra,
            }
        )

    def add(
        name: str,
        *,
        run_patch: dict[str, Any] | None = None,
        release_patch: dict[str, Any] | None = None,
        artifact_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        payload = {**deepcopy(published), **(release_patch or {})}
        encoded = json.dumps(payload)
        if encoded not in proof_archives:
            archive = root / f"{name}.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
                zipped.writestr("publication.json", encoded)
            proof_archives[encoded] = archive
        transport_case(
            name,
            "available",
            {
                "actions/workflows/deploy.yml/runs": {
                    "json": {
                        "workflow_runs": []
                        if (run_patch or {}).get("path")
                        == ".github/workflows/publish-typescript-release.yml"
                        else [{"id": 1, "created_at": NOW}]
                    }
                },
                "actions/workflows/publish-typescript-release.yml/runs": (
                    {"json": {"workflow_runs": [{"id": 1, "created_at": NOW}]}}
                    if (run_patch or {}).get("path")
                    == ".github/workflows/publish-typescript-release.yml"
                    else {"status": 404, "json": {"message": "Not Found"}}
                ),
                "actions/runs/1": {"json": {**run, **(run_patch or {})}},
                "actions/runs/1/artifacts": {
                    "json": {"artifacts": artifacts if artifact_rows is None else artifact_rows}
                },
                "actions/artifacts/3/zip": {"file": str(proof_archives[encoded])},
            },
        )

    add("trusted")
    corrupted = bytearray(
        Path(cases[0]["responses"]["actions/artifacts/3/zip"]["file"]).read_bytes()
    )
    central = corrupted.index(b"PK\x01\x02")
    checksum = struct.unpack_from("<I", corrupted, central + 16)[0]
    struct.pack_into("<I", corrupted, central + 16, checksum ^ 1)
    corrupted_archive = root / "corrupt-crc.zip"
    corrupted_archive.write_bytes(corrupted)
    corrupt_case = deepcopy(cases[0])
    corrupt_case["name"] = "proof_crc"
    corrupt_case["responses"]["actions/artifacts/3/zip"] = {"file": str(corrupted_archive)}
    cases.append(corrupt_case)
    run_refusals: dict[str, dict[str, Any]] = {
        "branch": {"head_branch": "staging"},
        "event": {"event": "pull_request"},
        "conclusion": {"conclusion": "failure"},
        "repository": {"head_repository": {"full_name": "other/repository"}},
        "workflow": {"path": ".github/workflows/other.yml"},
        "sha": {"head_sha": "c" * 40},
    }
    for name, patch in run_refusals.items():
        add(name, run_patch=patch)
    add("artifact_identity", release_patch={"artifact_id": 22})
    add("run_identity", release_patch={"publication_run_id": 22})
    add("mac_identity", artifact_rows=[*artifacts[:2], {**artifacts[2], "id": 99}])
    add("proof_expired", artifact_rows=[{**artifacts[0], "expired": True}, *artifacts[1:]])
    add("expired", release_patch={"expires_at": "2000-01-01T00:00:00+00:00"})
    add(
        "no_runtime",
        release_patch={"manifest": {**published["manifest"], "runtime_contract": None}},
    )
    runtime = published["manifest"]["runtime_contract"]
    runtime_edges: list[tuple[str, dict[str, Any]]] = [
        ("inventory_newline", {"inventory_sha256": "a" * 64 + "\n"}),
        ("feature_newline", {"cpu_features": [*runtime["cpu_features"], "extra\n"]}),
        ("version_newline", {"python_version": "3.12.12\n"}),
        ("unicode_patch", {"python_version": "3.12.１２"}),
    ]
    for name, patch in runtime_edges:
        add(
            name,
            release_patch={
                "manifest": {
                    **published["manifest"],
                    "runtime_contract": {**runtime, **patch},
                }
            },
        )
    node_runtime = {**runtime, "version": 2, "minimum_node_version": "22.18.0"}
    del node_runtime["python_version"]
    node_manifest = {**published["manifest"], "runtime_contract": node_runtime}
    node_workflow = {"path": ".github/workflows/publish-typescript-release.yml"}
    add("node_trusted", run_patch=node_workflow, release_patch={"manifest": node_manifest})
    add("deploy_node_mismatch", release_patch={"manifest": node_manifest})
    add("node_python_mismatch", run_patch=node_workflow)
    for name, patch in (
        ("node_minimum", {"minimum_node_version": "22.17.0"}),
        ("node_float_version", {"version": 2.0}),
        ("node_python_field", {"python_version": "3.12.12"}),
    ):
        add(
            name,
            run_patch=node_workflow,
            release_patch={
                "manifest": {**node_manifest, "runtime_contract": {**node_runtime, **patch}}
            },
        )
    transport_case(
        "combined_order",
        "available_order",
        {
            "actions/workflows/deploy.yml/runs": {
                "json": {
                    "workflow_runs": [
                        {"id": 9007199254740992, "created_at": "2026-11-12T10:00:00.000001Z"}
                    ]
                }
            },
            "actions/workflows/publish-typescript-release.yml/runs": {
                "json": {
                    "workflow_runs": [
                        {"id": 9007199254740993, "created_at": "2026-11-12T10:00:00.000001Z"},
                        {"id": 1, "created_at": "2026-11-12T10:00:00.000002Z"},
                    ]
                }
            },
        },
    )
    transport_case(
        "missing_deploy",
        "available",
        {
            "actions/workflows/deploy.yml/runs": {"status": 404, "json": {}},
        },
    )
    transport_case(
        "publisher_refused",
        "available",
        {
            "actions/workflows/deploy.yml/runs": {"json": {"workflow_runs": []}},
            "actions/workflows/publish-typescript-release.yml/runs": {"status": 403, "json": {}},
        },
    )
    source = root / "large-artifact.zip"
    with source.open("wb") as stream:
        for _ in range(128):
            chunk = b"unit21-streamed-artifact\n" * 2048
            stream.write(chunk)
    transport_case(
        "download",
        "download",
        {
            "actions/artifacts/2": {"json": {"expired": False}},
            "actions/artifacts/2/zip": {"file": str(source)},
        },
    )
    transport_case(
        "download_expired",
        "download",
        {
            "actions/artifacts/2": {"json": {"expired": True}},
        },
    )
    transport_case("transport_redaction", "publication", {"actions/runs/1": {"failure": True}})
    transport_case(
        "stream_redaction",
        "download",
        {
            "actions/artifacts/2": {"json": {"expired": False}},
            "actions/artifacts/2/zip": {"body_failure": True},
        },
    )
    for name, chunks, stall in [
        ("slow_steady", ["first", "second", "third", "fourth"], False),
        ("stalled_read", ["first"], True),
    ]:
        transport_case(
            name,
            "download",
            {
                "actions/artifacts/2": {"json": {"expired": False}},
                "actions/artifacts/2/zip": {"chunks": chunks, "chunk_ms": 30, "stall": stall},
            },
            inactivity_ms=70,
        )
    expected = []
    original_client = httpx.Client
    for index, case in enumerate(cases):
        seen: list[dict[str, Any]] = []

        class Body(httpx.SyncByteStream):
            def __init__(
                self, reply: dict[str, Any], http_request: httpx.Request, inactivity_ms: int
            ) -> None:
                self.reply, self.request = reply, http_request
                self.inactivity_ms = inactivity_ms

            def __iter__(self) -> Any:
                if self.reply.get("body_failure"):
                    raise httpx.ReadError(token.read_text(), request=self.request)
                if "chunks" in self.reply:
                    assert self.request.extensions["timeout"]["read"] == 60
                    for chunk in self.reply["chunks"]:
                        time.sleep(self.reply["chunk_ms"] / 1000)
                        yield chunk.encode()
                    if self.reply["stall"]:
                        time.sleep(self.inactivity_ms / 1000)
                        raise httpx.ReadTimeout("scripted stalled read", request=self.request)
                    return
                with Path(self.reply["file"]).open("rb") as stream:
                    while chunk := stream.read(65536):
                        yield chunk

        def handle(
            http_request: httpx.Request,
            case: dict[str, Any] = case,
            seen: list[dict[str, Any]] = seen,
        ) -> httpx.Response:
            path = http_request.url.path.removeprefix("/repos/owner/repository/")
            seen.append(
                {
                    "path": path,
                    "query": dict(http_request.url.params),
                    "authorized": http_request.headers.get("Authorization")
                    == f"Bearer {token.read_text().strip()}",
                }
            )
            reply = case["responses"][path]
            if reply.get("failure"):
                raise httpx.ConnectError(token.read_text(), request=http_request)
            if "file" in reply or "chunks" in reply or reply.get("body_failure"):
                body = Body(reply, http_request, case.get("inactivity_ms", 0))
                return httpx.Response(reply.get("status", 200), stream=body)
            return httpx.Response(reply.get("status", 200), json=reply.get("json"))

        def client(*args: Any, **kwargs: Any) -> httpx.Client:
            return original_client(*args, **kwargs, transport=httpx.MockTransport(handle))

        monkeypatch.setattr(httpx, "Client", client)
        provider = GitHubUpdateProvider("owner/repository", token)
        try:
            result: Any = None
            if case["action"] == "available":
                offered = provider.available()
                result = None if offered is None else asdict(offered)
            elif case["action"] == "available_order":
                considered: list[int] = []
                monkeypatch.setattr(
                    provider,
                    "publication",
                    lambda run_id, considered=considered: considered.append(run_id),
                )
                provider.available()
                result = {"considered": considered}
            elif case["action"] == "publication":
                offered = provider.publication(case["run_id"])
                result = None if offered is None else asdict(offered)
            else:
                destination = root / f"python-download-{index}.zip"
                provider.download_artifact(case["artifact_id"], destination)
                result = {
                    "sha256": hashlib.file_digest(destination.open("rb"), "sha256").hexdigest(),
                    "bytes": destination.stat().st_size,
                }
            expected.append({"name": case["name"], "result": result, "error": None, "seen": seen})
        except Exception as error:
            expected.append(
                {"name": case["name"], "result": None, "error": str(error), "seen": seen}
            )
    monkeypatch.setattr(httpx, "Client", original_client)
    actual = _node(
        tmp_path,
        {"root": str(root), "token_file": str(token), "cases": cases},
        "probe_update_provider",
    )
    timing = {item["name"]: item.pop("timing") for item in actual if "timing" in item}
    assert actual == json.loads(json.dumps(expected))
    assert next(item["result"] for item in actual if item["name"] == "combined_order") == {
        "considered": [1, 9007199254740993, 9007199254740992]
    }
    assert timing["slow_steady"]["elapsed_ms"] > 70
    assert timing["slow_steady"]["aborted"] is False
    assert timing["stalled_read"]["aborted"] is True
    assert {item["production_timeout"] for item in timing.values()} == {60_000}
    assert token.read_text().strip() not in json.dumps(actual)
    _report(
        request,
        "unit-21-update-provider.json",
        {
            "complete": True,
            "seconds": time.perf_counter() - started,
            "result": actual,
            "inactivity": timing,
            "download_bytes": source.stat().st_size,
            "boundary": (
                "Real GitHub adapters and scripted HTTP transports. "
                "Real ZIP proof and streamed files. No external network."
            ),
        },
    )
