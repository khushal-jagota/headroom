"""Compare metrics' typed rules and exact sidecar rows against current Python.

Store/rule parity compares 82 operations and exact rows/compressed bytes.
The retained runtime case proves IPC, ready/start costs, and reader replacement.
The sampler case proves cadence, ownership handoff, and a pending stop.
The native case proves query/parent-death cleanup through owned pidfds.
The route case proves public admission, validation, and error classification.
These reuse the existing data fixture and app hosts, without another listener.
"""

from __future__ import annotations

import copy
import ctypes
import json
import os
import selectors
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.support.synthetic_homes import create_synthetic_homes
from tests.unit.test_vps_metrics_data import sample

from planner.environments.vps_metrics import data
from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.logic.rates import counter_rate, host_cpu_percent

ROOT = Path(__file__).resolve().parents[3]
TABLES = {
    "metadata": "key",
    "samples": "seq",
    "snapshots": "seq",
    "aggregates": "resolution,bucket,boot,entity",
    "payload_totals": "kind",
}


def _node_executable() -> str:
    selected = shutil.which("node")
    if selected is None:
        raise FileNotFoundError("Node is absent from the caller's PATH")
    return str(Path(selected).resolve())


def _environment(root: Path) -> dict[str, str]:
    env = {
        "PATH": str(Path(_node_executable()).parent) + os.pathsep + os.defpath,
        "LANG": "C.UTF-8",
        "TMPDIR": str(root),
        "TMP": str(root),
        "TEMP": str(root),
        **create_synthetic_homes(root),
    }
    for key, name in {
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
        "PLAN_WORKSPACE_ROOT": "workspace",
        "PLAN_TICKET_SCRATCH_ROOT": "scratch",
        "PLAN_APP_ROOT": "app",
    }.items():
        path = root / name
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    config = root / "config.yaml"
    config.write_text("{}\n")
    env.update(
        {
            "PLAN_CONFIG_PATH": str(config),
            "PLAN_DB_PATH": str(root / "application.sqlite"),
            "PLAN_TICKET_SCRATCH_DATABASE": str(root / "application.sqlite"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "credential.key"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
            "PLAN_SERVER_URL": "http://127.0.0.1:0",
        }
    )
    return env


def _process_identity(pid: int) -> tuple[int, int]:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), int(fields[19])


def _owned_pidfd(pid: int, parent: int) -> int | None:
    if sys.platform != "linux":
        return None
    expected = _process_identity(pid)
    descriptor = os.pidfd_open(pid)
    try:
        assert _process_identity(pid) == expected and expected[0] == parent
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _launch_probe(
    root: Path,
    companion: str,
    function: str,
    specification: dict[str, Any],
) -> tuple[subprocess.Popen[bytes], int | None]:
    request = root / "request.json"
    request.write_text(json.dumps(specification, ensure_ascii=False))
    launcher = root / "probe.mjs"
    launcher.write_text(
        "import{readFileSync}from'node:fs';const m=await import(process.argv[2]);"
        "console.log(await m[process.argv[3]](readFileSync(process.argv[4],'utf8')));"
    )
    process = subprocess.Popen(
        [
            _node_executable(),
            str(launcher),
            (ROOT / f"server/environments/vps_metrics/testing/{companion}.ts").as_uri(),
            function,
            str(request),
        ],
        cwd=ROOT,
        env=_environment(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )
    try:
        return process, _owned_pidfd(process.pid, os.getpid())
    except BaseException:
        # This exact Popen child is fresh, unreaped, and not handed to another owner.
        # Its PID cannot be reused before waitpid. Popen.kill also checks its exit lock.
        process.kill()
        process.communicate(timeout=5)
        raise


def _run_probe(root: Path, companion: str, function: str, specification: dict[str, Any]) -> Any:
    process, descriptor = _launch_probe(root, companion, function, specification)
    try:
        output, errors = process.communicate(timeout=60)
        assert process.returncode == 0, errors
        return json.loads(output)
    finally:
        _cleanup(process, descriptor)


def _rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        table: [
            {
                key: {"hex": value.hex()} if isinstance(value, bytes) else value
                for key, value in dict(row).items()
            }
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
        ]
        for table, order in TABLES.items()
    }


def _python(spec: dict[str, Any], database: Path) -> dict[str, Any]:
    config = MetricsConfig(**spec["config"])
    store = data.Store(database, config)
    store.initialize()
    results = []
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        for operation in copy.deepcopy(spec["operations"]):
            result: Any = None
            error: dict[str, str] | None = None
            try:
                op = operation["op"]
                if op == "append":
                    if operation.get("nonfinite"):
                        operation["sample"]["metrics"]["cpu"]["value"] = float("nan")
                    result = store.append(operation["sample"])
                elif op == "prune":
                    store.prune(operation["now"])
                elif op == "latest":
                    result = store.latest(conn, operation["now"])
                elif op == "processes":
                    result = store.processes(conn, operation["now"], **operation["query"])
                elif op == "history":
                    result = store.history(conn, **operation["query"])
                elif op == "activity":
                    result = store.renew_activity(operation["now"])
                elif op == "activity_until":
                    result = store.activity_until()
                elif op == "failure":
                    store.record_failure(
                        operation["now"], operation["reason"], operation["cadence"]
                    )
                elif op == "reopen":
                    config = replace(config, **operation["config"])
                    store = data.Store(database, config)
                    store.initialize()
                elif op == "pinned_wal":
                    # Fixture-only table drives WAL frames, not domain state.
                    with sqlite3.connect(database) as pressure:
                        pressure.execute(
                            "CREATE TABLE IF NOT EXISTS parity_wal_pressure("
                            "id INTEGER PRIMARY KEY,payload BLOB NOT NULL)"
                        )
                    with (
                        closing(sqlite3.connect(database)) as pinned,
                        closing(sqlite3.connect(database)) as pressure,
                    ):
                        pinned.execute("BEGIN")
                        pinned.execute("SELECT COUNT(*) FROM samples").fetchone()
                        try:
                            for i in range(100):
                                pressure.execute(
                                    "INSERT INTO parity_wal_pressure(payload) VALUES(?)",
                                    (bytes([i]) * 4096,),
                                )
                                pressure.commit()
                                if Path(str(database) + "-wal").stat().st_size >= config.wal_bytes:
                                    break
                            try:
                                result = {"unexpected_sequence": store.append(operation["sample"])}
                            except Exception as exc:
                                result = {"type": type(exc).__name__, "message": str(exc)}
                        finally:
                            pinned.rollback()
                elif op == "expired_read":
                    with patch(
                        "planner.environments.vps_metrics.data.time.monotonic",
                        side_effect=[0.0] + [1.0] * 100,
                    ):
                        result = store.latest(conn, 0)
                else:
                    raise AssertionError(op)
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": str(exc)}
            results.append({"result": result, "error": error, "rows": _rows(conn)})
    rates = [
        counter_rate(
            item["current"],
            item["previous"],
            item["elapsed"],
            same_identity=item["same_identity"],
            unit=item["unit"],
            **({"scale": item["scale"]} if "scale" in item else {}),
        )
        for item in spec.get("rates", [])
    ]
    cpus = [
        host_cpu_percent(
            tuple(item["current"]),
            tuple(item["previous"]) if item["previous"] is not None else None,
            same_identity=item["same_identity"],
        )
        for item in spec.get("cpus", [])
    ]
    return {"operations": results, "rates": rates, "cpus": cpus}


def _case(name: str, config: dict[str, Any], operations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "config": config, "operations": operations}


def _cases() -> list[dict[str, Any]]:
    weighted: list[dict[str, Any]] = []
    for timestamp, value in [(50, 10), (65, 20.0), (80, 40), (200, 60), (215, None), (220, 100)]:
        current = sample(
            timestamp, 10 if value is None else value, boot="second" if timestamp == 220 else "boot"
        )
        current["metrics"]["tie-kind"] = {
            "value": 1 if timestamp <= 65 else 1.0,
            "unit": "count",
            "reason": None,
        }
        current["metrics"]["tie-sign"] = {
            "value": 0.0 if timestamp <= 65 else -0.0,
            "unit": "count",
            "reason": None,
        }
        current["services"] = [
            {
                "identity": "service-café",
                "name": "unit 😀",
                "kind": "service",
                "metrics": {"cpu": {"value": 500.0, "unit": "µ%", "reason": None}},
                "state": "active",
                "state_reason": None,
                "process_count": 1,
            }
        ]
        if value is None:
            current["metrics"]["cpu"].update(
                {"value": None, "reason": "counter reset", "duration": 15}
            )
        weighted.append({"op": "append", "sample": current})
    for resolution in ["raw", "minute", "coarse"]:
        weighted.append(
            {
                "op": "history",
                "query": {
                    "signal": "cpu",
                    "entity": None,
                    "start": 0,
                    "end": 1800,
                    "resolution": resolution,
                },
            }
        )
    weighted.extend(
        [
            {
                "op": "history",
                "query": {
                    "signal": "cpu",
                    "entity": "service-café",
                    "start": 0,
                    "end": 1800,
                    "resolution": "minute",
                },
            },
            {"op": "latest", "now": 220},
            {"op": "latest", "now": 1000},
            {"op": "expired_read"},
        ]
    )
    slow: list[dict[str, Any]] = []
    for timestamp, sampled_at in [(0, 0), (15, 0), (30, 0), (45, 0), (60, 60), (75, 60)]:
        current = sample(timestamp)
        current["metrics"]["cpu"]["sampled_at"] = sampled_at
        slow.append({"op": "append", "sample": current})
    slow.append(
        {
            "op": "history",
            "query": {
                "signal": "cpu",
                "entity": None,
                "start": 0,
                "end": 1800,
                "resolution": "minute",
            },
        }
    )
    first = sample(0)
    first["processes"] = [
        {
            "identity": {"boot_id": "boot", "pid": i, "start_ticks": 1},
            "parent_pid": 0,
            "uid": 0,
            "name": "short",
            "state": "S",
            "service": "x",
            "metrics": {},
        }
        for i in range(3)
    ]
    first["process_count"] = 3
    oversized = copy.deepcopy(first)
    oversized["timestamp"] = oversized["monotonic"] = 15
    oversized["processes"][0]["name"] = "x" * 1500
    processes: list[dict[str, Any]] = [
        {"op": "append", "sample": first},
        {"op": "append", "sample": oversized},
        *[
            {
                "op": "processes",
                "now": 15,
                "query": {"sequence": seq, "offset": offset, "limit": limit},
            }
            for seq, offset, limit in [
                (1, 0, 2),
                (1, 2, 2),
                (None, 0, 2),
                (999, 0, 2),
                (1, -1, 2),
                (1, 0, 251),
            ]
        ],
        {"op": "prune", "now": 30},
        {"op": "processes", "now": 30, "query": {"sequence": 1, "offset": 0, "limit": 2}},
    ]
    restart: list[dict[str, Any]] = [
        {"op": "append", "sample": sample(0)},
        {"op": "append", "sample": sample(15, 20)},
        {"op": "reopen", "config": {}},
        {"op": "append", "sample": sample(15, 20)},
        {"op": "prune", "now": 100},
        {"op": "append", "sample": sample(15, 20)},
        {"op": "activity", "now": 10},
        {"op": "activity", "now": 9},
        {"op": "activity_until"},
        {"op": "failure", "now": 11, "reason": "disk full", "cadence": 15},
        {"op": "latest", "now": 11},
        {"op": "append", "sample": sample(30), "nonfinite": True},
        {"op": "latest", "now": 1000},
    ]
    churn: list[dict[str, Any]] = []
    for timestamp in range(0, 301, 15):
        current = sample(timestamp)
        current["services"] = [
            {
                "identity": f"service-{timestamp}",
                "name": "unit",
                "kind": "service",
                "metrics": current["metrics"],
                "state": "active",
                "state_reason": None,
                "process_count": 0,
            }
        ]
        churn.append({"op": "append", "sample": current})
    churn.extend(
        [
            {
                "op": "history",
                "query": {
                    "signal": "cpu",
                    "entity": None,
                    "start": 0,
                    "end": 31,
                    "resolution": "raw",
                },
            },
            {"op": "latest", "now": 300},
            {"op": "prune", "now": 2000},
            {"op": "latest", "now": 2000},
        ]
    )
    cases = [
        _case("weighted-gaps-reboot-entities", {}, weighted),
        _case("cached-slow-source", {}, slow),
        _case(
            "immutable-process-pages",
            {"process_snapshot_bytes": 1000, "process_seconds": 20},
            processes,
        ),
        _case("durable-progress-activity-failure", {"raw_seconds": 1}, restart),
        _case(
            "age-byte-retention",
            {
                "metrics_payload_bytes": 1500,
                "process_payload_bytes": 300,
                "raw_seconds": 30,
                "minute_seconds": 90,
                "coarse_seconds": 120,
                "history_points": 2,
            },
            churn,
        ),
    ]
    cases[0]["rates"] = [
        {
            "current": after,
            "previous": before,
            "elapsed": elapsed,
            "same_identity": same,
            "unit": "bytes_per_second",
        }
        for before, after, elapsed, same in [
            (None, 10, 2, True),
            (10, 20, 2, False),
            (10, 1, 2, True),
            (10, 20, 0, True),
            (10, 20, -1, True),
            (10.0, 20.0, 2.0, True),
        ]
    ]
    cases[0]["cpus"] = [
        {"current": after, "previous": before, "same_identity": same}
        for before, after, same in [
            (None, [1, 0, 0, 1], True),
            ([3, 0, 0, 1], [2, 0, 0, 2], True),
            ([1, 0, 0, 1], [2, 0, 0, 2], True),
            ([1, 0, 0, 1], [1, 0, 0, 1], True),
        ]
    ]
    trial = sample(15, 20.0)
    trial_current: dict[str, Any] = dict(trial)
    snapshot_processes = trial_current.pop("processes")
    trial_current["processes"] = []
    snapshot = {
        "processes": snapshot_processes,
        "total": trial["process_count"],
        "availability": trial["process_availability"],
        "reason": trial["process_reason"],
    }
    base_bytes = (
        len(data._encode(trial_current).encode())
        + len(data._pack(trial_current))
        + len(data._pack(snapshot))
    )
    compressible = sample(30)
    compressible["metrics"]["cpu"]["unit"] = "a" * 100_000
    cases.append(
        _case(
            "append-rollback-and-plaintext-budget",
            {},
            [
                {"op": "append", "sample": sample(0)},
                {
                    "op": "reopen",
                    "config": {
                        "transaction_payload_bytes": base_bytes + 1,
                        "process_snapshot_bytes": 100,
                    },
                },
                {"op": "append", "sample": trial},
                {"op": "latest", "now": 15},
                {
                    "op": "reopen",
                    "config": {
                        "metrics_payload_bytes": 10_000,
                        "transaction_payload_bytes": 10_000,
                        "process_snapshot_bytes": 1000,
                    },
                },
                {"op": "append", "sample": compressible},
                {"op": "latest", "now": 30},
            ],
        )
    )
    huge = sample(15)
    huge["metrics"] = {
        f"metric-{i}": {"value": 1, "unit": "a" * 100, "reason": None} for i in range(100)
    }
    page_case = _case(
        "sqlite-page-cap-rollback",
        {},
        [{"op": "append", "sample": huge}, {"op": "latest", "now": 0}],
    )
    page_case["seed_page_cap"] = True
    cases.append(page_case)
    cases.append(
        _case(
            "pinned-wal-backpressure-and-recovery",
            {
                "wal_bytes": 32 * 1024,
                "transaction_payload_bytes": 16 * 1024,
                "process_snapshot_bytes": 8 * 1024,
            },
            [
                {"op": "append", "sample": sample(0)},
                {"op": "pinned_wal", "sample": sample(15)},
                {"op": "latest", "now": 15},
                {"op": "append", "sample": sample(400)},
                {"op": "latest", "now": 400},
            ],
        )
    )
    return cases


def test_unit21_metrics_store_and_rules_match_python(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    reports = []
    for case in _cases():
        root = tmp_path / case["name"]
        root.mkdir()
        if case.get("seed_page_cap"):
            seed = root / "seed.sqlite"
            seeded = data.Store(seed)
            seeded.initialize()
            seeded.append(sample(0))
            with closing(sqlite3.connect(seed)) as source:
                pages = source.execute("PRAGMA page_count").fetchone()[0]
                page_size = source.execute("PRAGMA page_size").fetchone()[0]
                for name in ("python.sqlite", "typescript.sqlite"):
                    with closing(sqlite3.connect(root / name)) as target:
                        source.backup(target)
            case["config"] = {"database_bytes": pages * page_size}
        expected = _python(case, root / "python.sqlite")
        if case["name"] == "append-rollback-and-plaintext-budget":
            for index, message in [
                (2, "VPS metrics aggregate exceeds transaction byte limit"),
                (5, "VPS metrics latest exceeds metrics payload budget"),
            ]:
                assert expected["operations"][index]["error"] == {
                    "type": "OperationalError",
                    "message": message,
                }
                assert (
                    expected["operations"][index]["rows"]
                    == expected["operations"][index - 1]["rows"]
                )
        elif case["name"] == "sqlite-page-cap-rollback":
            assert expected["operations"][0]["error"] == {
                "type": "OperationalError",
                "message": "database or disk is full",
            }
            assert expected["operations"][1]["result"]["sequence"] == 1
        elif case["name"] == "pinned-wal-backpressure-and-recovery":
            assert expected["operations"][1]["result"] == {
                "type": "OperationalError",
                "message": "VPS metrics WAL backpressure: checkpoint blocked",
            }
            assert expected["operations"][1]["rows"] == expected["operations"][0]["rows"]
            assert expected["operations"][3]["result"] == 2
        spec = {**case, "root": str(root), "database": str(root / "typescript.sqlite")}
        actual = _run_probe(root, "store_parity", "metrics_store_parity", spec)
        if actual != expected:
            (root / "difference.json").write_text(
                json.dumps({"python": expected, "typescript": actual}, indent=2, ensure_ascii=False)
            )
        assert actual == expected, f"{case['name']}: see {root / 'difference.json'}"
        reports.append(
            {
                "case": case["name"],
                "operations": len(case["operations"]),
                "exact_rows_and_compressed_bytes": True,
            }
        )
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-21-metrics-store.json").write_text(
            json.dumps(
                {
                    "kind": "direct",
                    "equal": True,
                    "cases": reports,
                    "seconds": time.perf_counter() - started,
                },
                indent=2,
            )
            + "\n"
        )


def _runtime(
    root: Path,
    mode: str,
    config: dict[str, Any] | None = None,
) -> tuple[subprocess.Popen[bytes], int | None]:
    return _launch_probe(
        root,
        "runtime",
        "metrics_runtime_probe",
        {
            "root": str(root),
            "database": str(root / "metrics.sqlite"),
            "sample": sample(0),
            "mode": mode,
            "config": config or {},
        },
    )


def _line(process: subprocess.Popen[bytes], seconds: float = 30) -> dict[str, Any]:
    assert process.stdout
    deadline = time.monotonic() + seconds
    line = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while not line.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            assert remaining > 0 and selector.select(remaining) and time.monotonic() < deadline, (
                "owned runtime did not emit its next state"
            )
            # No text/read-ahead buffer can hide the next complete state from select.
            value = os.read(process.stdout.fileno(), 1)
            assert value, "owned runtime exited without its state"
            line.extend(value)
    return cast(dict[str, Any], json.loads(line))


def _finish(process: subprocess.Popen[bytes], seconds: float = 10) -> dict[str, Any]:
    process.wait(timeout=seconds)
    assert process.stdout and process.stderr
    output, errors = process.stdout.read(), process.stderr.read()
    assert process.returncode == 0, errors
    return cast(dict[str, Any], json.loads(output.splitlines()[-1]))


def _children(process: subprocess.Popen[bytes]) -> list[int]:
    return [
        int(pid)
        for pid in Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
    ]


def _cleanup(process: subprocess.Popen[bytes], descriptor: int | None) -> None:
    try:
        if process.poll() is None:
            if descriptor is not None:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                # Non-Linux cases retain their own unreaped Popen child, never a /proc PID.
                process.kill()
        process.communicate(timeout=5)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _report(request: pytest.FixtureRequest, name: str, report: dict[str, Any]) -> None:
    folder = request.config.getoption("--compare-report-dir")
    if folder:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(json.dumps(report, indent=2) + "\n")


@pytest.mark.skipif(
    sys.platform != "linux", reason="cost evidence inspects owned Linux proc entries"
)
def test_unit21_metrics_independent_runtime_reads_and_cost(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    database = tmp_path / "metrics.sqlite"
    source = data.Store(database)
    source.initialize()
    source.append(sample(0))
    source.append(sample(15, 20.0))
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        expected = {
            "latest": source.latest(conn, 100),
            "page": source.processes(conn, 100, sequence=1, offset=0, limit=250),
            "history": source.history(
                conn, signal="cpu", entity=None, start=0, end=100, resolution="minute"
            ),
        }
    process, descriptor = _runtime(tmp_path, "plain", {"read_seconds": 1})
    try:
        ready = _line(process)
        assert ready["type"] == "ready_cost"
        owned = _children(process)
        assert len(owned) == 2
        rss = {
            pid: int(
                next(
                    line.split()[1]
                    for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                    if line.startswith("VmRSS:")
                )
            )
            * 1024
            for pid in [process.pid, *owned]
        }
        (tmp_path / "cost-ack").touch()
        actual = _finish(process)
        for key, value in expected.items():
            assert actual[key] == value
        assert actual["activity"] == 130
        replacement = actual["replacement"]
        assert {
            key: replacement[key]
            for key in (
                "expired_during_backoff",
                "replacement_failed_before_ready",
                "successful_sequences",
                "boot_count",
                "restored_readers",
            )
        } == {
            "expired_during_backoff": True,
            "replacement_failed_before_ready": True,
            "successful_sequences": [2, 2],
            "boot_count": 5,
            "restored_readers": 2,
        }
        assert not (tmp_path / "collection.jsonl").exists()
        assert not (tmp_path / "application.sqlite").exists()
        assert all(not Path(f"/proc/{pid}").exists() for pid in owned)
        with sqlite3.connect(database, timeout=0.1) as conn:
            assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        _report(
            request,
            "unit-21-metrics-runtime.json",
            {
                "equal_python_responses": True,
                "collector_none_does_no_host_work": True,
                "startup_ms": actual["startup_ms"],
                "cold_ms": actual["cold_ms"],
                "warm_ms": actual["warm_ms"],
                "activity_ms": actual["activity_ms"],
                "stop_ms": actual["stop_ms"],
                "rss_bytes_by_owned_pid": rss,
                "summed_rss_bytes_includes_shared_pages": sum(rss.values()),
                "all_owned_readers_reaped_and_wal_clear": True,
                "replacement": replacement,
            },
        )
    finally:
        _cleanup(process, descriptor)


def _assert_lock(path: Path, held: bool) -> None:
    import fcntl

    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            assert held
        else:
            assert not held


def test_unit21_metrics_sampler_cadence_ownership_and_pending_stop(tmp_path: Path) -> None:
    config = {
        "idle_seconds": 0.08,
        "active_seconds": 0.02,
        "activity_check_seconds": 0.005,
        "ownership_retry_seconds": 0.01,
        "slow_seconds": 0.1,
        "prune_seconds": 0.1,
    }
    normal = tmp_path / "normal"
    normal.mkdir()
    process, descriptor = _runtime(normal, "sampler", {**config, "slow_seconds": 30})
    try:
        result = _finish(process, seconds=20)
        assert result["latest"]["sample"] is not None
        assert result["latest"]["sample"]["expected_interval"] == config["active_seconds"]
        assert result["failure"] is None
        events = [
            json.loads(line) for line in (normal / "collection.jsonl").read_text().splitlines()
        ]
        starts = [event for event in events if event["type"] == "start"]
        assert starts[0]["include_slow"] is True
        observed = starts[result["blocked_start_count"] : result["blocked_end_count"]]
        assert len(observed) >= 3
        assert any(event["include_slow"] is False for event in observed)
        # The active transition can sample immediately. Later active gaps obey cadence.
        stable = observed[1:]
        assert all(
            b["monotonic"] - a["monotonic"] >= 0.015
            for a, b in zip(stable, stable[1:], strict=False)
        )
        _assert_lock(normal / "metrics.sqlite.lock", False)
    finally:
        _cleanup(process, descriptor)
    for mode in ["hung", "short_hung", "startup_failure"]:
        root = tmp_path / mode
        root.mkdir()
        process, descriptor = _runtime(root, mode, config)
        try:
            if mode == "startup_failure":
                assert _finish(process) == {"startup_error": "scripted startup failure"}
                continue
            pending = _line(process)
            assert pending["type"] == "stopped_pending"
            if mode == "hung":
                assert _finish(process) == {
                    "settled": True,
                    "owner": False,
                    "contender_took_over": True,
                    "collector_gate_unreleased": True,
                }
                original_events = [
                    json.loads(line)
                    for line in (root / "collection.jsonl").read_text().splitlines()
                ]
                assert all(event["type"] != "finish" for event in original_events)
                with sqlite3.connect(root / "metrics.sqlite") as conn:
                    assert conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0] >= 1
            else:
                # Preserve PR811's binary coalesced-record read while the new
                # policy proves natural process exit after actual termination.
                assert _line(process) == {"terminated_worker": True, "owner": False}
                assert process.wait(timeout=10) == 0
        finally:
            _cleanup(process, descriptor)
        _assert_lock(root / "metrics.sqlite.lock", False)


def _pathological_samples(database: Path) -> None:
    with sqlite3.connect(database) as conn:
        conn.execute("ALTER TABLE samples RENAME TO stored_samples")
        conn.execute("""CREATE VIEW samples AS
            WITH RECURSIVE c(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM c WHERE x<1000000000)
            SELECT x seq,x timestamp,'boot' boot,x monotonic,X'' payload,0 bytes FROM c""")


def _write_and_checkpoint(database: Path) -> int:
    with sqlite3.connect(database, timeout=0.02) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS probe_pressure(value TEXT)")
        conn.execute("INSERT INTO probe_pressure VALUES('new WAL evidence')")
        conn.commit()
        return int(conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0])


@pytest.mark.skipif(
    sys.platform != "linux", reason="owned-process death proof uses Linux subreaper cleanup"
)
def test_unit21_metrics_native_deadline_and_parent_death_release_wal(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    # The test adopts its own orphans for exact cleanup. Production uses portable JS only.
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    reports = []
    try:
        for mode in ["main_blocked", "parent_killed"]:
            root = tmp_path / mode
            root.mkdir()
            process, descriptor = _runtime(
                root,
                "blocked_read",
                {
                    "read_seconds": 0.3 if mode == "main_blocked" else 5,
                    "read_workers": 1 if mode == "main_blocked" else 2,
                },
            )
            owned: list[int] = []
            handles: dict[int, int] = {}
            reaped: set[int] = set()
            try:
                assert _line(process)["type"] == "ready"
                owned = _children(process)
                assert len(owned) == (1 if mode == "main_blocked" else 2)
                for pid in owned:
                    handle = _owned_pidfd(pid, process.pid)
                    assert handle is not None
                    handles[pid] = handle
                _pathological_samples(root / "metrics.sqlite")
                (root / "query-ready").touch()
                assert _line(process)["type"] == "blocking"
                assert _write_and_checkpoint(root / "metrics.sqlite") == 1
                observed = time.monotonic()
                if mode == "parent_killed":
                    assert descriptor is not None
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    process.wait(timeout=3)
                time.sleep(0.6)
                with selectors.DefaultSelector() as deaths:
                    for handle in handles.values():
                        deaths.register(handle, selectors.EVENT_READ)
                    assert len(deaths.select(timeout=0)) == len(handles)
                for pid in owned:
                    if mode == "parent_killed":
                        found, _status = os.waitpid(pid, os.WNOHANG)
                        assert found == pid
                        reaped.add(pid)
                assert _write_and_checkpoint(root / "metrics.sqlite") == 0
                observed_ms = (time.monotonic() - observed) * 1000
                if mode == "main_blocked":
                    assert process.poll() is None
                    # A refreshed queued deadline would now return successfully. The original
                    # admission deadline must reject it before any new snapshot begins.
                    with sqlite3.connect(root / "metrics.sqlite") as conn:
                        conn.execute("DROP VIEW samples")
                        conn.execute("ALTER TABLE stored_samples RENAME TO samples")
                    result = _finish(process)
                    assert "unexpected" not in result["results"]
                    assert result["stopped"] is True
                reports.append(
                    {
                        "mode": mode,
                        "observed_after_ms": observed_ms,
                        "native_query_reader_dead": True,
                        "wal_clear": True,
                        "pidfd_cleanup": True,
                    }
                )
            finally:
                try:
                    _cleanup(process, descriptor)
                finally:
                    try:
                        for pid, handle in handles.items():
                            if pid not in reaped:
                                try:
                                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                        # A failed partial capture happens before the query gate. All original
                        # READY readers then exit through parent EOF, even without a pidfd.
                        remaining = set(owned) - reaped
                        deadline = time.monotonic() + 3
                        while remaining and time.monotonic() < deadline:
                            for pid in tuple(remaining):
                                try:
                                    found, _status = os.waitpid(pid, os.WNOHANG)
                                except ChildProcessError:
                                    remaining.remove(pid)
                                else:
                                    if found:
                                        remaining.remove(pid)
                            if remaining:
                                time.sleep(0.005)
                        assert not remaining, (
                            f"owned readers did not exit after APP EOF: {remaining}"
                        )
                    finally:
                        for handle in handles.values():
                            os.close(handle)
    finally:
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0
    _report(request, "unit-21-metrics-native-lifetime.json", {"reports": reports})


def test_unit21_metrics_routes_populated_errors_and_callers_match_python(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tests.compare.direct.test_unit21_updates import _assert_route_callers, _seed_route_callers

    from planner.core.db import connect, create_schema
    from planner.environments.vps_metrics.api import router
    from planner.environments.vps_metrics.runtime import MetricsRuntime

    began = time.perf_counter()
    source_application = tmp_path / "python-application.sqlite"
    with closing(connect(str(source_application))) as connection:
        create_schema(connection)
        callers = _seed_route_callers(connection)
        with closing(sqlite3.connect(tmp_path / "application.sqlite")) as copied:
            connection.backup(copied)
            _assert_route_callers(copied, callers)
    config: dict[str, Any] = {"process_page_rows": 2, "history_points": 2}
    source_path = tmp_path / "python.sqlite"
    source = data.Store(source_path, MetricsConfig(**config))
    source.initialize()
    for timestamp in [0, 15, 30]:
        current = sample(timestamp, 10.0 + timestamp)
        current["processes"] = [
            {
                "identity": {"boot_id": "boot", "pid": pid, "start_ticks": 1},
                "parent_pid": 0,
                "uid": 0,
                "name": f"process-{pid}",
                "state": "S",
                "service": "fixture.service",
                "metrics": {},
            }
            for pid in [1, 2]
        ]
        current["process_count"] = 2
        source.append(current)
    target = tmp_path / "typescript.sqlite"
    with closing(sqlite3.connect(source_path)) as origin, closing(sqlite3.connect(target)) as dest:
        origin.backup(dest)
    base = [
        {"method": "GET", "path": "/api/vps-metrics/latest"},
        {"method": "GET", "path": "/api/vps-metrics/processes?sequence=1&limit=1"},
        {"method": "GET", "path": "/api/vps-metrics/history?signal=cpu&start=0&end=60"},
        {"method": "POST", "path": "/api/vps-metrics/activity"},
    ]
    operations: list[dict[str, Any]] = list(base)
    worker = {"x-plan-actor": "worker", "x-plan-ticket-id": callers["ticket"]}
    operations.extend({**operation, "headers": worker} for operation in base)
    operations.extend(
        {**base[0], "headers": headers}
        for headers in [
            {"x-plan-actor": "chief"},
            {"x-plan-actor": "manager", "x-plan-manager-id": callers["manager"]},
        ]
    )
    public_count = len(operations)
    operations.extend(
        [
            {"method": "GET", "path": "/api/vps-metrics/processes?sequence=0&offset=-1&limit=0"},
            {"method": "GET", "path": "/api/vps-metrics/processes?limit=3"},
            {"method": "GET", "path": "/api/vps-metrics/processes?sequence=999&limit=2"},
            {
                "method": "GET",
                "path": "/api/vps-metrics/processes?sequence=999&sequence=1&limit=3&limit=2",
            },
            {"method": "GET", "path": "/api/vps-metrics/history"},
            {
                "method": "GET",
                "path": "/api/vps-metrics/history?signal=&start=bad&end=nope&resolution=bad&entity="
                + "x" * 501,
            },
            {"method": "GET", "path": "/api/vps-metrics/history?signal=cpu&start=nan&end=60"},
            {
                "method": "GET",
                "path": "/api/vps-metrics/history?signal=cpu&start=0&end=60&resolution=raw",
            },
        ]
    )
    for field, method, path, action in [
        ("latest", "GET", "latest", "corrupt"),
        ("snapshot", "GET", "processes?sequence=1&limit=2", "corrupt"),
        ("activity", "POST", "activity", "corrupt"),
        ("samples", "GET", "latest", "hide"),
        ("metadata", "POST", "activity", "hide"),
    ]:
        operations.extend(
            {
                "method": method,
                "path": "/api/vps-metrics/" + path,
                "fault": f"{change}_{field}",
            }
            for change in [action, "restore"]
        )
    runtime = MetricsRuntime(
        source_path, source, None, MetricsConfig(**config), wall_clock=lambda: 100.0
    )
    runtime.start()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.state.vps_metrics = runtime
    saved: dict[str, Any] = {}
    expected = []
    try:
        with (
            TestClient(app, raise_server_exceptions=False) as client,
            closing(sqlite3.connect(source_path)) as faults,
        ):
            for operation in operations:
                if "fault" in operation:
                    action, field = operation["fault"].split("_")
                    with faults:
                        if field in {"metadata", "samples"}:
                            old = field if action == "hide" else "hidden_" + field
                            new = "hidden_" + field if action == "hide" else field
                            faults.execute(f"ALTER TABLE {old} RENAME TO {new}")
                        elif field == "snapshot":
                            if field not in saved:
                                saved[field] = faults.execute(
                                    "SELECT payload FROM snapshots WHERE seq=1"
                                ).fetchone()[0]
                            faults.execute(
                                "UPDATE snapshots SET payload=? WHERE seq=1",
                                (b"\x00\x01\x02" if action == "corrupt" else saved[field],),
                            )
                        else:
                            if field not in saved:
                                saved[field] = faults.execute(
                                    "SELECT value FROM metadata WHERE key=?", (field,)
                                ).fetchone()[0]
                            faults.execute(
                                "UPDATE metadata SET value=? WHERE key=?",
                                ("invalid JSON" if action == "corrupt" else saved[field], field),
                            )
                response = client.request(
                    operation["method"], operation["path"], headers=operation.get("headers")
                )
                expected.append(
                    {
                        "status": response.status_code,
                        "body": response.json()
                        if response.headers.get("content-type", "").startswith("application/json")
                        else response.text,
                    }
                )
    finally:
        asyncio.run(runtime.stop(deadline=time.monotonic() + 1))
    assert all(item["status"] == 200 for item in expected[:public_count])
    assert expected[0]["body"]["sample"]["metrics"]
    assert expected[1]["body"]["processes"]
    assert expected[2]["body"]["points"]
    assert expected[3]["body"] == {"active_until": 130.0}
    assert [item["status"] for item in expected[-10:]] == [
        500,
        200,
        500,
        200,
        500,
        200,
        503,
        200,
        503,
        200,
    ]
    actual = _run_probe(
        tmp_path,
        "routes",
        "metrics_route_probe",
        {
            "root": str(tmp_path),
            "database": str(target),
            "config": config,
            "requests": operations,
        },
    )
    if actual["responses"] != expected:
        (tmp_path / "routes-difference.json").write_text(
            json.dumps({"python": expected, "typescript": actual["responses"]}, indent=2)
        )
    assert actual["responses"] == expected, str(tmp_path / "routes-difference.json")
    assert {item["status"] for item in expected} == {200, 410, 422, 500, 503}
    _report(
        request,
        "unit-21-metrics-routes.json",
        {
            "equal": True,
            "callers": callers,
            "requests": len(operations),
            "seconds": time.perf_counter() - began,
            "boundary": (
                "Existing createApp/Fastify inject and FastAPI TestClient, actual sampler/reader "
                "IPC, copied Python sidecar, and persisted Manager/Ticket callers in the "
                "copied application database. No listener or browser."
            ),
        },
    )
