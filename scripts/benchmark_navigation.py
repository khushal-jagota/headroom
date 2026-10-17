"""Compare producer cost on isolated copies of one accepted-predecessor fixture.

Run this same absolute script with each worktree's own interpreter. The controller
never imports planner. No backend, server, provider, live database or real home is used.
Run only after candidate correctness passes. This measures generic source producer
cost; it does not certify Signal fields, work/unread totals or browser freshness.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SOURCE_TABLES = (
    "projects",
    "worker_types",
    "chief_settings",
    "sprint_items",
    "agents",
    "tickets",
    "conversations",
    "ticket_conversations",
    "conversation_events",
    "conversation_lifecycle_facts",
    "conversation_error_acknowledgements",
    "ticket_blocks",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--database-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--cold-pairs", type=int, default=3)
    parser.add_argument(
        "--finalizer-symbol",
        help="Candidate synchronous precommit callback (default: planner.core.db:finalize)",
    )
    parser.add_argument("--worker", choices=("seed", "run"), help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", default="updates", help=argparse.SUPPRESS)
    return parser.parse_args()


def isolated_environment(root: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PLAN_") and k not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update(
        {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "xdg"),
            "PLAN_DB_PATH": str(root / "planner.db"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_HERMES_HOME": str(root / "hermes"),
            "HERMES_HOME": str(root / "hermes"),
            "CODEX_HOME": str(root / "codex"),
            "CLAUDE_CONFIG_DIR": str(root / "claude"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    for name in ("home", "xdg", "logs", "hermes", "codex", "claude"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return env


def provenance(tree: Path) -> dict[str, str]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(tree), *args], text=True).strip()

    return {
        "tree": str(tree),
        "head": git("rev-parse", "HEAD"),
        "status": git("status", "--short"),
        "requirements_sha256": hashlib.sha256((tree / "requirements.txt").read_bytes()).hexdigest(),
    }


def source_digest(conn: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for table in SOURCE_TABLES:
        rows = sorted(
            json.dumps(list(row), separators=(",", ":"))
            for row in conn.execute(f'SELECT * FROM "{table}"')
        )
        digest.update((table + "\n" + "\n".join(rows)).encode())
    return digest.hexdigest()


def seed(db: Path) -> dict[str, Any]:
    from planner.conversation.events import (
        AgentMessageEventPayload,
        conversation_event_payload_to_canonical_json,
    )
    from planner.conversation.message_content import MessageText
    from planner.core.db import connect, create_schema

    conn = connect(str(db))
    create_schema(conn)
    payload = conversation_event_payload_to_canonical_json(
        AgentMessageEventPayload((MessageText("Synthetic historical event."),))
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        for i in range(100):
            parent = f"si_benchmark_{i:04d}"
            conversation = f"benchmark_parent_{i:04d}"
            conn.execute(
                "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,"
                "supervisor_model,created_at,updated_at,settled) VALUES (?,?,?,'codex',"
                "'gpt-5.6-sol',1,1,?)",
                (parent, f"Synthetic parent {i}", "project_other", i % 3 == 0),
            )
            conn.execute(
                "INSERT INTO agents(agent_key,conversation_id) VALUES (?,?)",
                (f"sprint_item_supervisor_{parent}", conversation),
            )
        for i in range(400):
            cid = f"benchmark_parent_{i:04d}" if i < 100 else f"benchmark_ticket_{i:04d}"
            parent = f"si_benchmark_{i % 100:04d}"
            identity = json.dumps(
                [["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", parent]]
            )
            history = 1000 if i in (0, 100, 200) else 4
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
                "access,identity_environment_variables,latest_sequence,created_at,"
                "latest_agent_activity_sequence,latest_agent_activity_at) "
                "VALUES (?,'codex','gpt-5.6-sol',?,'full',?,?,1,?,1)",
                (cid, str(db.parent / "workspace"), identity, history, history),
            )
            conn.executemany(
                "INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) "
                "VALUES (?,?,'agent_message',?,1)",
                ((cid, sequence, payload) for sequence in range(1, history + 1)),
            )
            conn.execute(
                "INSERT INTO conversation_lifecycle_facts(conversation_id,kind,identity,"
                "turn_number,"
                "operation,status) VALUES (?,'turn','1',1,'turn','completed')",
                (cid,),
            )
        for i in range(1000):
            parent_number = 0 if i < 500 else 1 + i % 99
            parent = f"si_benchmark_{parent_number:04d}"
            conversation = f"benchmark_ticket_{100 + i % 300:04d}"
            proposal = (
                json.dumps(
                    {
                        "field": "success_condition",
                        "body": "Synthetic proposal",
                        "proposed_by": "worker",
                        "created_at": 1,
                    }
                )
                if i % 5 == 0
                else None
            )
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,"
                "employee_launch_model,stage,"
                "ceiling,project_id,sprint_item_id,orchestrator_kind,field_values,created_at,updated_at,"
                "conversation_id,pending_proposal,pending_proposal_revision) "
                "VALUES (?,?,'coding','codex','gpt-5.6-sol','needs_success_condition',"
                "'needs_success_condition','project_other',?,'sprint_item','{}',1,1,?,?,?)",
                (
                    f"t_benchmark_{i:04d}",
                    f"Synthetic ticket {i}",
                    parent,
                    conversation,
                    proposal,
                    1 if proposal else 0,
                ),
            )
            # Current aliases deliberately share conversations. The canonical history table
            # permits one owner per conversation, so only its first owner receives a row.
            conn.execute(
                "INSERT OR IGNORE INTO ticket_conversations(conversation_id,ticket_id) "
                "VALUES (?,?)",
                (conversation, f"t_benchmark_{i:04d}"),
            )
        for i in range(20):
            conn.execute(
                "INSERT INTO ticket_blocks(blocking_ticket_id,blocked_ticket_id) VALUES (?,?)",
                (f"t_benchmark_{i:04d}", f"t_benchmark_{i + 100:04d}"),
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    result = {
        "fixture_source_digest": source_digest(conn),
        "tickets": 1000,
        "parents": 100,
        "conversations": 400,
        "event_rows": conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0],
    }
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return result


def navigation_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "navigation_journal" not in tables:
        return {"journal_batches": 0, "journal_bytes": 0, "records": 0, "upserts": 0, "deletes": 0}
    count, size = conn.execute(
        "SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM navigation_journal"
    ).fetchone()
    # JSON payload metrics are outside timed transaction regions.
    upserts = deletes = 0
    for row in conn.execute("SELECT body FROM navigation_journal"):
        batch = json.loads(row[0])
        upserts += len(batch.get("upserts", []))
        deletes += len(batch.get("deletes", batch.get("deletions", [])))
    return {
        "journal_batches": count,
        "journal_bytes": size,
        "records": conn.execute("SELECT COUNT(*) FROM navigation_records").fetchone()[0],
        "upserts": upserts,
        "deletes": deletes,
    }


def mutate(conn: sqlite3.Connection, index: int) -> None:
    ticket = f"t_benchmark_{index % 1000:04d}"
    kind = index % 8
    conn.execute("BEGIN IMMEDIATE")
    if kind == 0:
        conn.execute(
            "UPDATE tickets SET title=?,updated_at=? WHERE id=?",
            (f"Updated {index}", index + 2, ticket),
        )
    elif kind == 1:
        conn.execute(
            "UPDATE tickets SET sprint_item_id=?,updated_at=? WHERE id=?",
            (f"si_benchmark_{(index + 17) % 100:04d}", index + 2, ticket),
        )
    elif kind == 2:
        conn.execute(
            "UPDATE sprint_items SET settled=1-settled,updated_at=? WHERE id=?",
            (index + 2, f"si_benchmark_{index % 100:04d}"),
        )
    elif kind == 3:
        conn.execute(
            "UPDATE tickets SET pending_proposal=NULL,"
            "pending_proposal_revision=pending_proposal_revision+1 "
            "WHERE id=?",
            (ticket,),
        )
    elif kind == 4:
        from planner.conversation.lifecycle import (
            LifecycleFact,
            LifecycleKind,
            LifecycleOperation,
            LifecycleStatus,
        )
        from planner.conversation.lifecycle_storage import apply_facts

        conversation = f"benchmark_ticket_{100 + index % 300:04d}"
        attempt = f"benchmark:{index}"
        for status in (LifecycleStatus.intent, LifecycleStatus.unknown):
            apply_facts(
                conn,
                conversation,
                (
                    LifecycleFact(
                        LifecycleKind.attempt, attempt, index + 2, LifecycleOperation.prompt, status
                    ),
                ),
            )
    elif kind == 5:
        conn.execute(
            "UPDATE conversations SET owner_read_through_sequence=latest_sequence "
            "WHERE conversation_id=?",
            (f"benchmark_ticket_{100 + index % 300:04d}",),
        )
    elif kind == 6:
        conn.execute(
            "UPDATE projects SET name=? WHERE id='project_other'", (f"Benchmark project {index}",)
        )
    else:
        conn.execute(
            "UPDATE worker_types SET updated_at=? WHERE worker_type='coding'", (index + 2,)
        )
    conn.execute("COMMIT")


def runtime_provenance() -> dict[str, str]:
    import planner

    tree = Path.cwd().resolve()
    source = Path(planner.__file__).resolve()
    prefix = Path(sys.prefix).resolve()
    if not source.is_relative_to(tree / "src") or prefix != tree / ".venv":
        raise RuntimeError(
            f"runtime provenance mismatch: source={source}, prefix={prefix}, cwd={tree}"
        )
    return {
        "cwd": str(tree),
        "planner_source": str(source),
        "sys_prefix": str(prefix),
        "interpreter": sys.executable,
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
    }


def run_worker(args: argparse.Namespace) -> dict[str, Any]:
    runtime = runtime_provenance()
    from planner.core.db import connect, create_schema

    root = args.database_root.resolve()
    live = Path("/home/vps/Deployments/Panels/current/data").resolve()
    if (
        root == live
        or root.is_relative_to(live)
        or not any(part.startswith("navigation-producer-") for part in root.parts)
    ):
        raise SystemExit("worker requires a controller-created synthetic runtime")
    db = root / "planner.db"
    if db.exists() or db.is_symlink():
        raise SystemExit("worker refuses an existing destination database")
    if args.worker == "seed":
        return {**seed(db), "runtime_provenance": runtime}
    assert args.fixture is not None
    fixture_path = args.fixture.resolve()
    if fixture_path.is_relative_to(live) or not any(
        part.startswith("navigation-producer-") for part in fixture_path.parts
    ):
        raise SystemExit("worker refuses a fixture outside the synthetic controller runtime")
    # Immutable synthetic fixture only. This URI is never supplied by a runtime fallback.
    with sqlite3.connect(args.fixture.resolve().as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(db) as destination:
            source.backup(destination)
    initialization_start = time.perf_counter_ns()
    conn = connect(str(db))
    create_schema(conn)
    initialization_ns = time.perf_counter_ns() - initialization_start
    initial_digest = source_digest(conn)
    before = navigation_metrics(conn)
    conn.close()
    importlib.import_module("planner.conversation.lifecycle_storage")
    local = threading.local()
    if args.finalizer_symbol:
        module_name, function_name = args.finalizer_symbol.split(":", 1)
        module = importlib.import_module(module_name)
        original = getattr(module, function_name)

        def timed_finalizer(*values: Any, **keywords: Any) -> Any:
            started = time.perf_counter_ns()
            try:
                return original(*values, **keywords)
            finally:
                local.projection_ns = (
                    getattr(local, "projection_ns", 0) + time.perf_counter_ns() - started
                )

        setattr(module, function_name, timed_finalizer)
    workers = int(args.case[-1]) if args.case.startswith("writers") else 1
    count = 20 if args.case == "burst" else 80
    interval_ns = 10_000_000 if args.case == "burst" else 0
    origin = time.perf_counter_ns() + 5_000_000
    results: list[dict[str, Any]] = []

    def operation(index: int) -> dict[str, Any]:
        offer = origin + index * interval_ns
        if interval_ns:
            remaining = offer - time.perf_counter_ns()
            if remaining > 0:
                time.sleep(remaining / 1e9)
        connection = getattr(local, "connection", None)
        if connection is None:
            connection = local.connection = connect(str(db))
        local.projection_ns = 0
        changes_before = connection.total_changes
        start = time.perf_counter_ns()
        failure = None
        try:
            mutate(connection, index)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
            if connection.in_transaction:
                connection.execute("ROLLBACK")
        end = time.perf_counter_ns()
        return {
            "index": index,
            "operation": index % 8,
            "offer_ns": offer - origin,
            "start_ns": start - origin,
            "end_ns": end - origin,
            "transaction_ns": end - start,
            "backlog_ns": max(0, start - offer) if interval_ns else 0,
            "projection_ns": local.projection_ns if args.finalizer_symbol else None,
            "changed_rows": connection.total_changes - changes_before,
            "failure": failure,
        }

    if workers == 1:
        results = [operation(i) for i in range(count)]
        if hasattr(local, "connection"):
            local.connection.close()
    else:
        # Each worker owns one sqlite connection. All offers start together, with no retry.
        barrier = threading.Barrier(workers)

        def writer(number: int) -> list[dict[str, Any]]:
            barrier.wait()
            try:
                return [operation(i) for i in range(number, count, workers)]
            finally:
                if hasattr(local, "connection"):
                    local.connection.close()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            for partition in executor.map(writer, range(workers)):
                results.extend(partition)
    conn = connect(str(db))
    after = navigation_metrics(conn)
    final_digest = source_digest(conn)
    metrics = {key: after[key] - before[key] for key in after}
    violations = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
    conn.close()
    elapsed = max(row["end_ns"] for row in results) - min(row["start_ns"] for row in results)
    successful = sum(row["failure"] is None for row in results)
    return {
        "case": args.case,
        "runtime_provenance": runtime,
        "initialization_ns": initialization_ns,
        "initial_source_digest": initial_digest,
        "final_source_digest": final_digest,
        "operations": sorted(results, key=lambda row: row["index"]),
        "navigation_delta": metrics,
        "foreign_key_violations": violations,
        "successful": successful,
        "failed": count - successful,
        "busy_failures": sum("locked" in (row["failure"] or "").lower() for row in results),
        "achieved_per_second": successful * 1e9 / elapsed,
        "offered_per_second": 100 if interval_ns else None,
        "maximum_backlog_ns": max(row["backlog_ns"] for row in results),
        "database_bytes": db.stat().st_size,
        "wal_bytes": Path(str(db) + "-wal").stat().st_size
        if Path(str(db) + "-wal").exists()
        else 0,
    }


def controller(args: argparse.Namespace) -> None:
    if args.baseline is None or args.candidate is None or args.output is None:
        raise SystemExit("--baseline, --candidate and --output are required")
    root = args.database_root.resolve()
    live = Path("/home/vps/Deployments/Panels/current/data").resolve()
    if root == live or root.is_relative_to(live) or root in (Path("/"), Path.home().resolve()):
        raise SystemExit(
            "database-root must be a dedicated isolated directory outside live runtime"
        )
    root.mkdir(parents=True, exist_ok=True)
    baseline = args.baseline.resolve()
    candidate = args.candidate.resolve()
    harness = Path(__file__).resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def invoke(
        tree: Path, directory: Path, mode: str, case: str = "updates", fixture: Path | None = None
    ) -> dict[str, Any]:
        command = [
            str(tree / ".venv/bin/python"),
            str(harness),
            "--worker",
            mode,
            "--database-root",
            str(directory),
            "--case",
            case,
        ]
        if fixture:
            command += ["--fixture", str(fixture)]
        if tree == candidate:
            command += ["--finalizer-symbol", args.finalizer_symbol or "planner.core.db:finalize"]
        started = time.perf_counter_ns()
        result = subprocess.run(
            command,
            cwd=tree,
            env=isolated_environment(directory),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"{tree.name} {case}: {result.stderr[-6000:]}")
        data: dict[str, Any] = json.loads(result.stdout)
        data["process_wall_ns"] = time.perf_counter_ns() - started
        return data

    with tempfile.TemporaryDirectory(prefix="navigation-producer-", dir=root) as temporary:
        run_root = Path(temporary)
        fixture_root = run_root / "fixture"
        seed_result = invoke(baseline, fixture_root, "seed")
        fixture = fixture_root / "planner.db"
        with args.output.open("w") as output:

            def emit(data: dict[str, Any]) -> None:
                output.write(json.dumps(data, sort_keys=True) + "\n")
                output.flush()

            emit(
                {
                    "type": "provenance",
                    "baseline": provenance(baseline),
                    "candidate": provenance(candidate),
                    "seed": seed_result,
                    "python": sys.version,
                    "host": os.uname().nodename,
                    "semantics": "generic producer only; final Signal/UI semantics unsatisfied",
                    "cold_definition": "new process and fixture copy; OS cache not evicted",
                }
            )
            pairs = [("updates", i) for i in range(args.trials)]
            pairs += [("cold", i) for i in range(args.cold_pairs)]
            pairs += [(case, i) for case in ("burst", "writers2", "writers4") for i in range(3)]
            for ordinal, (case, pair) in enumerate(pairs):
                order = [("baseline", baseline), ("candidate", candidate)]
                if ordinal % 2:
                    order.reverse()
                for label, tree in order:
                    directory = run_root / f"{case}-{pair}-{label}"
                    result = invoke(
                        tree, directory, "run", "updates" if case == "cold" else case, fixture
                    )
                    if result["initial_source_digest"] != seed_result["fixture_source_digest"]:
                        raise RuntimeError(f"source fixture mismatch for {label} {case} {pair}")
                    result.update(
                        type="trial", label=label, case=case, pair=pair, order=ordinal % 2
                    )
                    values = [
                        row["transaction_ns"]
                        for row in result["operations"]
                        if row["failure"] is None
                    ]
                    result["transaction_median_ns"] = statistics.median(values) if values else None
                    result["transaction_range_ns"] = [min(values), max(values)] if values else None
                    emit(result)
                    print(
                        f"{case} pair {pair + 1}: {label}, failures={result['failed']}",
                        file=sys.stderr,
                        flush=True,
                    )


if __name__ == "__main__":
    parsed = arguments()
    if parsed.worker:
        print(json.dumps(run_worker(parsed)))
    else:
        controller(parsed)
