"""Profile generic navigation producer work on a synthetic predecessor fixture.

Diagnostic instrumentation changes timing. Use benchmark_navigation.py for matched
producer measurements after correctness passes. No provider or live runtime is used.
"""

from __future__ import annotations

import argparse
import collections
import cProfile
import json
import pstats
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import benchmark_navigation as benchmark

SCENARIOS = (
    "parent_project",
    "ticket_title",
    "read_ack",
    "lifecycle",
    "worker_definition",
    "long_lifecycle",
    "settled_lifecycle",
    "settled_read",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    parser.add_argument("--comparison-tree", type=Path)
    parser.add_argument("--no-profile", action="store_true")
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--seed-tree", type=Path, required=True)
    parser.add_argument("--database-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--settled-facts", type=int, default=5000)
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=SCENARIOS)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=SCENARIOS, help=argparse.SUPPRESS)
    return parser.parse_args()


def add_unknown_facts(conn: sqlite3.Connection, count: int) -> None:
    """Add unresolved uncertainty. These are not irrelevant settled history."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "INSERT INTO conversation_lifecycle_facts(conversation_id,kind,identity,"
            "turn_number,operation,status) VALUES('benchmark_ticket_0104','attempt',?,?,'prompt',"
            "'unknown')",
            [(f"settled:{i}", i + 1) for i in range(count)],
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def add_settled_facts(
    conn: sqlite3.Connection, count: int, conversation: str = "benchmark_ticket_0104"
) -> None:
    """Add completed historic turns without any live capability or provider call."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "INSERT INTO conversation_lifecycle_facts(conversation_id,kind,identity,"
            "turn_number,operation,status) VALUES(?,'turn',?,?,'turn','completed')",
            [(conversation, str(i + 2), i + 2) for i in range(count)],
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def replace_attribute(target: Any, name: str, value: Any) -> None:
    setattr(target, name, value)


def shape(sql: str) -> str:
    sql = re.sub(r"'(?:''|[^'])*'", "?", sql)
    sql = re.sub(r"\b[0-9]+\b", "?", sql)
    return " ".join(sql.split())


def profile_worker(args: argparse.Namespace) -> dict[str, Any]:
    runtime = benchmark.runtime_provenance()
    from planner.core import db as database
    from planner.navigation import data as navigation

    root = args.database_root.resolve()
    live = Path("/home/vps/Deployments/Panels/current/data").resolve()
    if root.is_relative_to(live) or not any(
        p.startswith("navigation-producer-") for p in root.parts
    ):
        raise RuntimeError("profile worker requires a synthetic controller runtime")
    assert args.fixture is not None and args.scenario is not None
    fixture = args.fixture.resolve()
    if fixture.is_relative_to(live) or not any(
        p.startswith("navigation-producer-") for p in fixture.parts
    ):
        raise RuntimeError("profile refuses a fixture outside synthetic runtime")
    path = root / "planner.db"
    if path.exists() or path.is_symlink():
        raise RuntimeError("profile refuses an existing destination database")
    with sqlite3.connect(fixture.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target)
    conn = database.connect(str(path))
    database.create_schema(conn)
    if args.scenario == "long_lifecycle":
        add_unknown_facts(conn, args.settled_facts)
    elif args.scenario in ("settled_lifecycle", "settled_read"):
        add_settled_facts(
            conn,
            args.settled_facts,
            "benchmark_ticket_0105" if args.scenario == "settled_read" else "benchmark_ticket_0104",
        )
    source_before = benchmark.source_digest(conn)
    before = benchmark.navigation_metrics(conn)
    if args.no_profile:
        return unprofiled_operation(conn, args, runtime, source_before, before)
    execute_counts: collections.Counter[str] = collections.Counter()
    bulk_counts: collections.Counter[str] = collections.Counter()
    traced: collections.Counter[str] = collections.Counter()
    derives: collections.Counter[tuple[str, str]] = collections.Counter()
    puts: collections.Counter[tuple[str, str]] = collections.Counter()
    original_execute = database.ParticipatingCursor.execute
    original_many = database.ParticipatingCursor.executemany
    original_derive: Any = navigation._derive
    original_put: Any = navigation._put_root

    def execute(cursor: Any, sql: str, parameters: Any = ()) -> Any:
        execute_counts[shape(sql)] += 1
        return original_execute(cursor, sql, parameters)

    def many(cursor: Any, sql: str, parameters: Any) -> Any:
        bulk_counts[shape(sql)] += len(parameters) if hasattr(parameters, "__len__") else -1
        return original_many(cursor, sql, parameters)

    def derive(
        connection: sqlite3.Connection, key: tuple[str, str], *values: Any, **options: Any
    ) -> Any:
        derives[key] += 1
        return original_derive(connection, key, *values, **options)

    def put(
        connection: sqlite3.Connection, key: tuple[str, str], *values: Any, **options: Any
    ) -> Any:
        puts[key] += 1
        return original_put(connection, key, *values, **options)

    replace_attribute(database.ParticipatingCursor, "execute", execute)
    replace_attribute(database.ParticipatingCursor, "executemany", many)
    replace_attribute(navigation, "_derive", derive)
    replace_attribute(navigation, "_put_root", put)
    conn.set_trace_callback(lambda sql: traced.update([shape(sql)]))
    profiler = cProfile.Profile()
    changes_before = conn.total_changes
    started = time.perf_counter_ns()
    profiler.enable()
    try:
        if args.scenario == "parent_project":
            benchmark.mutate_fanout(conn, 0)
        elif args.scenario == "worker_definition":
            benchmark.mutate_fanout(conn, 1)
        else:
            benchmark.mutate(
                conn,
                {
                    "ticket_title": 0,
                    "read_ack": 5,
                    "lifecycle": 4,
                    "long_lifecycle": 4,
                    "settled_lifecycle": 4,
                    "settled_read": 5,
                }[args.scenario],
            )
    finally:
        profiler.disable()
        elapsed_ns = time.perf_counter_ns() - started
        conn.set_trace_callback(None)
        replace_attribute(database.ParticipatingCursor, "execute", original_execute)
        replace_attribute(database.ParticipatingCursor, "executemany", original_many)
        replace_attribute(navigation, "_derive", original_derive)
        replace_attribute(navigation, "_put_root", original_put)
    changed_rows = conn.total_changes - changes_before
    after = benchmark.navigation_metrics(conn)
    final_source = benchmark.source_digest(conn)
    last_batch = json.loads(
        conn.execute(
            "SELECT body FROM navigation_journal ORDER BY revision DESC LIMIT 1"
        ).fetchone()[0]
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    profile_path = args.output_root / f"{args.scenario}.prof"
    profiler.dump_stats(profile_path)
    stats = pstats.Stats(profiler)
    functions = []
    raw_stats: Any = stats
    for (filename, line, function), (
        primitive,
        total,
        self_time,
        cumulative,
        _callers,
    ) in raw_stats.stats.items():
        functions.append(
            {
                "file": filename,
                "line": line,
                "function": function,
                "primitive_calls": primitive,
                "total_calls": total,
                "self_seconds": self_time,
                "cumulative_seconds": cumulative,
            }
        )
    conn.close()
    return {
        "scenario": args.scenario,
        "runtime": runtime,
        "source_before": source_before,
        "source_after": final_source,
        "profiled_elapsed_ns": elapsed_ns,
        "changed_rows": changed_rows,
        "navigation_delta": {key: after[key] - before[key] for key in after},
        "public_upserts_by_kind": dict(
            collections.Counter(r["kind"] for r in last_batch["upserts"])
        ),
        "derive_calls_by_kind": dict(
            collections.Counter(key[0] for key, n in derives.items() for _ in range(n))
        ),
        "put_calls_by_kind": dict(
            collections.Counter(key[0] for key, n in puts.items() for _ in range(n))
        ),
        "duplicate_derive_calls": sum(n - 1 for n in derives.values()),
        "logical_execute_count": sum(execute_counts.values()),
        "logical_execute_shapes": execute_counts.most_common(),
        "bulk_rows_by_shape": bulk_counts.most_common(),
        "trace_events_including_trigger_repeats": sum(traced.values()),
        "trace_shapes": traced.most_common(),
        "functions_by_cumulative": sorted(
            functions, key=lambda f: f["cumulative_seconds"], reverse=True
        ),
        "historical_fixture_facts": (
            args.settled_facts if args.scenario in ("long_lifecycle", "settled_lifecycle") else 0
        ),
        "history_meaning": (
            "unknown/unresolved attempts"
            if args.scenario == "long_lifecycle"
            else "completed historic turns"
            if args.scenario == "settled_lifecycle"
            else "ordinary fixture"
        ),
        "profile_artifact": str(profile_path),
    }


def unprofiled_operation(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    runtime: dict[str, str],
    source_before: str,
    before: dict[str, int],
) -> dict[str, Any]:
    from planner.conversation import lifecycle_storage  # preload before timing
    from planner.core import db as database

    del lifecycle_storage
    database_module: Any = database
    original = database_module.finalize
    finalizer_ns = 0

    def finalizer(*values: Any, **options: Any) -> Any:
        nonlocal finalizer_ns
        started = time.perf_counter_ns()
        try:
            return original(*values, **options)
        finally:
            finalizer_ns += time.perf_counter_ns() - started

    replace_attribute(database, "finalize", finalizer)
    changes_before = conn.total_changes
    started = time.perf_counter_ns()
    failure = None
    try:
        benchmark.mutate(conn, 5 if args.scenario == "settled_read" else 4)
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
        if conn.in_transaction:
            conn.rollback()
    finally:
        elapsed_ns = time.perf_counter_ns() - started
        replace_attribute(database, "finalize", original)
    changes = conn.total_changes - changes_before
    after = benchmark.navigation_metrics(conn)
    source_after = benchmark.source_digest(conn)
    violations = [list(row) for row in conn.execute("PRAGMA foreign_key_check")]
    conn.close()
    return {
        "scenario": args.scenario,
        "runtime": runtime,
        "source_before": source_before,
        "source_after": source_after,
        "transaction_ns": elapsed_ns,
        "finalizer_ns": finalizer_ns,
        "non_finalizer_ns": elapsed_ns - finalizer_ns,
        "changed_rows": changes,
        "historical_completed_turns": args.settled_facts,
        "navigation_delta": {key: after[key] - before[key] for key in after},
        "failure": failure,
        "foreign_key_violations": violations,
        "instrumentation": (
            "Only the synchronous finalizer timer. Setup/digests/metrics outside clock."
        ),
    }


def matched_controller(args: argparse.Namespace) -> None:
    if args.comparison_tree is None:
        raise ValueError("--no-profile requires --comparison-tree")
    tree = args.tree.resolve()
    comparison = args.comparison_tree.resolve()
    root = args.database_root.resolve()
    live = Path("/home/vps/Deployments/Panels/current/data").resolve()
    if root.is_relative_to(live) or root in (Path("/"), Path.home().resolve()):
        raise RuntimeError("matched history benchmark requires isolated runtime")
    root.mkdir(parents=True, exist_ok=True)
    args.output_root.mkdir(parents=True, exist_ok=True)
    seed_tree = args.seed_tree.resolve()
    with tempfile.TemporaryDirectory(prefix="navigation-producer-history-", dir=root) as temporary:
        runtime = Path(temporary)
        fixture_root = runtime / "fixture"
        seed_command = [
            str(seed_tree / ".venv/bin/python"),
            str(Path(benchmark.__file__).resolve()),
            "--worker",
            "seed",
            "--database-root",
            str(fixture_root),
        ]
        seed = subprocess.run(
            seed_command,
            cwd=seed_tree,
            env=benchmark.isolated_environment(fixture_root),
            capture_output=True,
            text=True,
            check=True,
        )
        with (args.output_root / "settled-history-cost.jsonl").open("w") as output:
            output.write(
                json.dumps(
                    {
                        "type": "provenance",
                        "before": benchmark.provenance(comparison),
                        "after": benchmark.provenance(tree),
                        "fixture": json.loads(seed.stdout),
                        "setup": "5000 completed turns, both sides outside operation clock",
                    }
                )
                + "\n"
            )
            output.flush()
            for scenario in ("settled_read", "settled_lifecycle"):
                for pair in range(args.pairs):
                    order = [("before", comparison), ("after", tree)]
                    if pair % 2:
                        order.reverse()
                    receipts = []
                    for label, side in order:
                        child_root = runtime / f"{scenario}-{pair}-{label}"
                        command = [
                            str(side / ".venv/bin/python"),
                            str(Path(__file__).resolve()),
                            "--worker",
                            "--no-profile",
                            "--scenario",
                            scenario,
                            "--seed-tree",
                            str(seed_tree),
                            "--database-root",
                            str(child_root),
                            "--fixture",
                            str(fixture_root / "planner.db"),
                            "--output-root",
                            str(args.output_root),
                            "--settled-facts",
                            str(args.settled_facts),
                        ]
                        result = subprocess.run(
                            command,
                            cwd=side,
                            env=benchmark.isolated_environment(child_root),
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if result.returncode:
                            raise RuntimeError(f"history {scenario}/{label}: {result.stderr}")
                        receipt = json.loads(result.stdout)
                        receipt.update(type="trial", label=label, pair=pair)
                        receipts.append(receipt)
                        output.write(json.dumps(receipt, sort_keys=True) + "\n")
                        output.flush()
                        print(
                            f"history {scenario} pair {pair + 1}: {label}",
                            file=sys.stderr,
                            flush=True,
                        )
                    if (
                        len({r["source_before"] for r in receipts}) != 1
                        or len({r["source_after"] for r in receipts}) != 1
                    ):
                        raise RuntimeError("matched history source parity failed")


def controller(args: argparse.Namespace) -> None:
    tree = args.tree.resolve()
    seed_tree = args.seed_tree.resolve()
    root = args.database_root.resolve()
    live = Path("/home/vps/Deployments/Panels/current/data").resolve()
    if root.is_relative_to(live) or root in (Path("/"), Path.home().resolve()):
        raise RuntimeError("profile requires an isolated runtime root")
    root.mkdir(parents=True, exist_ok=True)
    args.output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="navigation-producer-profile-", dir=root) as temporary:
        runtime = Path(temporary)
        fixture_root = runtime / "fixture"
        seed_command = [
            str(seed_tree / ".venv/bin/python"),
            str(Path(benchmark.__file__).resolve()),
            "--worker",
            "seed",
            "--database-root",
            str(fixture_root),
        ]
        seed = subprocess.run(
            seed_command,
            cwd=seed_tree,
            env=benchmark.isolated_environment(fixture_root),
            capture_output=True,
            text=True,
            check=True,
        )
        fixture_receipt = json.loads(seed.stdout)
        with (args.output_root / "profiles.jsonl").open("w") as output:
            output.write(
                json.dumps(
                    {
                        "type": "provenance",
                        "tree": benchmark.provenance(tree),
                        "fixture": fixture_receipt,
                        "instrumentation": (
                            "cProfile + SQL shapes + sqlite trace. Diagnostic timings only."
                        ),
                    }
                )
                + "\n"
            )
            output.flush()
            for scenario in args.scenarios:
                child_root = runtime / scenario
                command = [
                    str(tree / ".venv/bin/python"),
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--scenario",
                    scenario,
                    "--seed-tree",
                    str(seed_tree),
                    "--database-root",
                    str(child_root),
                    "--fixture",
                    str(fixture_root / "planner.db"),
                    "--output-root",
                    str(args.output_root),
                    "--settled-facts",
                    str(args.settled_facts),
                ]
                result = subprocess.run(
                    command,
                    cwd=tree,
                    env=benchmark.isolated_environment(child_root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(f"profile {scenario}: {result.stderr}")
                receipt = json.loads(result.stdout)
                if (
                    scenario not in ("long_lifecycle", "settled_lifecycle", "settled_read")
                    and receipt["source_before"] != fixture_receipt["fixture_source_digest"]
                ):
                    raise RuntimeError("profile initial source fixture mismatch")
                output.write(json.dumps(receipt, sort_keys=True) + "\n")
                output.flush()
                print(f"profile {scenario} complete", file=sys.stderr, flush=True)


if __name__ == "__main__":
    parsed = arguments()
    if parsed.worker:
        print(json.dumps(profile_worker(parsed)))
    elif parsed.no_profile:
        matched_controller(parsed)
    else:
        controller(parsed)
