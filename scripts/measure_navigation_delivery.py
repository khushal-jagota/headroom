"""Measure synthetic commit-to-complete-frame delivery over real loopback HTTP.

This is a backend comparator, never an original-browser baseline or UI visibility proof.
Run with this worktree's interpreter, --output FILE --runtime-parent TEMP_PARENT.
All state is freshly created below TEMP_PARENT and removed after the measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import socket
import statistics
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema
from planner.navigation import data as navigation
from planner.navigation import delivery
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.delivery_contracts import batch_frame, compact_json


def distribution(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {
        "count": len(values),
        "minimum": ordered[0],
        "median": statistics.median(values),
        "p95_nearest_rank": ordered[math.ceil(len(values) * 0.95) - 1],
        "maximum": ordered[-1],
    }


def isolate(root: Path) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            del os.environ[key]
    for name in ("logs", "skills", "hermes", "codex", "claude", "workspace"):
        (root / name).mkdir()
    os.environ.update(
        {
            "PLAN_DB_PATH": str(root / "planner.db"),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_SKILLS_DIR": str(root / "skills"),
            "PLAN_HERMES_HOME": str(root / "hermes"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "0",
            "HERMES_HOME": str(root / "hermes"),
            "CLAUDE_CONFIG_DIR": str(root / "claude"),
        }
    )
    # No agent is spawned, no backend is built, and no external provider is contacted.


def seed(path: Path) -> None:
    with contextlib.closing(connect(str(path))) as conn:
        create_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,"
            "access,created_at) VALUES('measure','codex',?,'full',0)",
            (str(path.parent / "workspace"),),
        )
        conn.executemany(
            "INSERT INTO conversation_lifecycle_facts(conversation_id,kind,identity,turn_number,"
            "operation,status) VALUES('measure','turn',?,?,'turn','completed')",
            [(f"settled-{i}", i + 1) for i in range(5000)],
        )
        conn.executemany(
            "INSERT INTO conversation_lifecycle_facts(conversation_id,kind,identity,turn_number,"
            "operation,status) VALUES('measure','attempt',?,?,'prompt','unknown')",
            [(f"unresolved-{i}", i + 1) for i in range(1000)],
        )
        conn.executemany(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,employee_launch_model,"
            "stage,ceiling,project_id,field_values,created_at,updated_at,conversation_id) "
            "VALUES(?,?,'coding','codex','gpt-5.6-sol','needs_success_condition',"
            "'needs_success_condition','project_other','{}',1,1,'measure')",
            [(f"t_measure_{i:04d}", f"Synthetic ticket {i}") for i in range(500)],
        )
        conn.execute(
            "INSERT INTO projects(id,name,created_at,updated_at) "
            "VALUES('measure_delete','Delete fixture',1,1)"
        )
        conn.commit()


class Consumer:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.records = {(r["kind"], r["id"]): r for r in snapshot["records"]}
        self.revision = int(snapshot["revision"])
        self.frames: dict[int, dict[str, Any]] = {}
        self.cursor = ""
        self.connected = asyncio.Event()
        self.error: BaseException | None = None

    async def run(self, client: httpx.AsyncClient, cursor: str) -> None:
        try:
            async with client.stream(
                "GET", "/api/navigation/events", params={"cursor": cursor}
            ) as r:
                r.raise_for_status()
                self.connected.set()
                pending = b""
                async for chunk in r.aiter_raw():
                    chunk_received_ns = time.perf_counter_ns()
                    pending += chunk
                    while b"\n\n" in pending:
                        frame, pending = pending.split(b"\n\n", 1)
                        received = chunk_received_ns
                        if b"event: reset" in frame:
                            raise RuntimeError(f"unexpected reset: {frame!r}")
                        if b"event: batch" not in frame:
                            continue
                        lines = frame.splitlines()
                        batch = json.loads(
                            next(line[6:] for line in lines if line.startswith(b"data: "))
                        )
                        assert int(batch["base_revision"]) == self.revision
                        for record in batch["upserts"]:
                            self.records[(record["kind"], record["id"])] = record
                        for key in batch["deletes"]:
                            self.records.pop((key["kind"], key["id"]), None)
                        self.revision = int(batch["revision"])
                        self.cursor = next(
                            line[4:].decode() for line in lines if line.startswith(b"id: ")
                        )
                        self.frames[self.revision] = {
                            "received_ns": received,
                            "bytes": len(frame) + 2,
                            "upserts": len(batch["upserts"]),
                            "deletes": len(batch["deletes"]),
                        }
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.error = exc
            self.connected.set()
            raise

    async def reached(self, revision: int) -> None:
        async with asyncio.timeout(30):
            while self.revision < revision:
                if self.error:
                    raise self.error
                await asyncio.sleep(0.001)


async def measure_runtime(path: Path, label: str, full: bool) -> dict[str, Any]:
    config = DeliveryConfig()
    runtime = delivery.DeliveryRuntime(path, config)
    request_counts: dict[str, int] = {}
    writer_lock = threading.Lock()
    page_highwater = {"batches": 0, "serialized_bytes": 0}
    original_page = delivery.read_page

    def measured_page(*args: Any, **kwargs: Any) -> Any:
        page = original_page(*args, **kwargs)
        page_highwater["batches"] = max(page_highwater["batches"], len(page["batches"]))
        page_highwater["serialized_bytes"] = max(
            page_highwater["serialized_bytes"], sum(len(batch_frame(b)) for b in page["batches"])
        )
        return page

    delivery.read_page = measured_page

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # This is the real exclusive runtime lease and reconciliation, not an injected owner.
        ownership = ConversationStore(str(path)).claim_runtime()
        try:
            with contextlib.closing(connect(str(path))) as conn:
                conn.execute("BEGIN IMMEDIATE")
                navigation.reset_epoch(conn)
                conn.commit()
            await runtime.start()
            yield
        finally:
            await runtime.close()
            ownership.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(runtime.router)

    @app.middleware("http")
    async def count_requests(request: Any, call_next: Any) -> Any:
        key = f"{request.method} {request.url.path}"
        request_counts[key] = request_counts.get(key, 0) + 1
        return await call_next(request)

    @app.post("/benchmark/change")
    def change(body: dict[str, Any]) -> dict[str, Any]:
        begin = time.perf_counter_ns()
        with writer_lock, contextlib.closing(connect(str(path))) as conn:
            conn.execute("BEGIN IMMEDIATE")
            scenario = body["scenario"]
            if scenario == "fanout":
                conn.execute("UPDATE tickets SET title=? || id", (body["tag"],))
            elif scenario == "empty":
                conn.execute("UPDATE tickets SET title=title WHERE id='t_measure_0000'")
            elif scenario == "delete":
                conn.execute("DELETE FROM projects WHERE id='measure_delete'")
            else:
                conn.execute("UPDATE tickets SET title=? WHERE id='t_measure_0000'", (body["tag"],))
            conn.commit()
            committed = time.perf_counter_ns()  # first operation after successful commit returns
            revision = int(conn.execute("SELECT revision FROM navigation_meta").fetchone()[0])
        return {
            "commit_return_ns": committed,
            "write_started_ns": begin,
            "revision": revision,
            "scenario": scenario,
            "tag": body["tag"],
        }

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))  # OS surveys/reserves an available port atomically
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error", lifespan="on", access_log=False
        )
    )
    started = time.perf_counter_ns()
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    consumer_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(30):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("server exited during startup")
                await asyncio.sleep(0.001)
        startup_ms = (time.perf_counter_ns() - started) / 1e6
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False
        ) as client:
            response = await client.get("/api/navigation/snapshot")
            response.raise_for_status()
            initial = response.json()
            consumer = Consumer(initial["snapshot"])
            consumer.cursor = initial["cursor"]
            consumer_task = asyncio.create_task(consumer.run(client, consumer.cursor))
            await consumer.connected.wait()
            samples: list[dict[str, Any]] = []
            refreshes: list[dict[str, Any]] = []
            max_backlog = 0

            async def commit(
                scenario: str, tag: str, offered_ns: int | None = None
            ) -> dict[str, Any]:
                nonlocal max_backlog
                sent = time.perf_counter_ns()
                r = await client.post("/benchmark/change", json={"scenario": scenario, "tag": tag})
                r.raise_for_status()
                sample = {
                    **r.json(),
                    "offered_ns": offered_ns or sent,
                    "request_sent_ns": sent,
                    "response_received_ns": time.perf_counter_ns(),
                }
                max_backlog = max(max_backlog, sample["revision"] - consumer.revision)
                samples.append(sample)
                return sample

            for trial in range(10 if full else 1):
                sample = await commit("warm" if full else "cold", f"{label}-warm-{trial}")
                await consumer.reached(sample["revision"])
                before = time.perf_counter_ns()
                refresh = await client.get("/api/navigation/snapshot")
                refresh.raise_for_status()
                refreshes.append(
                    {
                        "duration_ms": (time.perf_counter_ns() - before) / 1e6,
                        "bytes": len(refresh.content),
                        "revision": sample["revision"],
                    }
                )
            burst_start = burst_end = None
            if full:
                burst_start = time.perf_counter_ns()
                tasks = []
                for trial in range(20):
                    offered = burst_start + trial * 10_000_000
                    await asyncio.sleep(max(0, (offered - time.perf_counter_ns()) / 1e9))
                    tasks.append(asyncio.create_task(commit("burst", f"burst-{trial}", offered)))
                burst = await asyncio.gather(*tasks)
                await consumer.reached(max(s["revision"] for s in burst))
                burst_end = time.perf_counter_ns()
                consumer_task.cancel()
                await asyncio.gather(consumer_task, return_exceptions=True)
                consumer_task = None
                for trial in range(5):
                    await commit("reconnect", f"disconnected-{trial}")
                reconnect_start = time.perf_counter_ns()
                consumer.connected.clear()
                consumer_task = asyncio.create_task(consumer.run(client, consumer.cursor))
                await consumer.reached(max(s["revision"] for s in samples))
                reconnect_ms = (time.perf_counter_ns() - reconnect_start) / 1e6
                fanout = asyncio.create_task(commit("fanout", "fanout-"))
                await asyncio.sleep(0.001)
                foreground = asyncio.create_task(commit("foreground", "foreground-amid-fanout"))
                await asyncio.gather(fanout, foreground)
                await consumer.reached(max(s["revision"] for s in samples))
                for scenario in ("empty", "delete"):
                    sample = await commit(scenario, scenario)
                    await consumer.reached(sample["revision"])
            else:
                reconnect_ms = None
            authoritative = await client.get("/api/navigation/snapshot")
            authoritative.raise_for_status()
            final = authoritative.json()["snapshot"]
            assert consumer.revision == int(final["revision"])
            assert (
                sorted(consumer.records.values(), key=lambda r: (r["kind"], r["id"]))
                == final["records"]
            )
            assert (
                next(r for r in final["records"] if r["kind"] == "conversation")["value"][
                    "lifecycle_summary"
                ]["attempt_status_counts"]["unknown"]
                == 1000
            )
            for sample in samples:
                frame = consumer.frames[sample["revision"]]
                sample.update(frame)
                sample["commit_to_complete_frame_ms"] = (
                    frame["received_ns"] - sample["commit_return_ns"]
                ) / 1e6
                sample["write_ms"] = (sample["commit_return_ns"] - sample["write_started_ns"]) / 1e6
            with contextlib.closing(connect(str(path))) as conn:
                count, size = conn.execute(
                    "SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) "
                    "FROM navigation_journal"
                ).fetchone()
            return {
                "label": label,
                "runtime_start_ms": startup_ms,
                "requests": request_counts,
                "initial_snapshot_bytes": len(response.content),
                "final_snapshot_bytes": len(authoritative.content),
                "samples": samples,
                "refresh_samples": refreshes,
                "exact_replay_equality": True,
                "latency_ms": {
                    scenario: distribution(
                        [
                            s["commit_to_complete_frame_ms"]
                            for s in samples
                            if s["scenario"] == scenario
                        ]
                    )
                    for scenario in sorted({s["scenario"] for s in samples})
                },
                "burst": {
                    "count": 20 if full else 0,
                    "offered_interval_ms": 10,
                    "offered_rate_per_second": 100,
                    "achieved_rate_to_last_complete_frame_per_second": 20
                    / ((burst_end - burst_start) / 1e9)
                    if burst_end is not None and burst_start is not None
                    else None,
                },
                "reconnect_catchup_ms": reconnect_ms,
                "journal_batches": count,
                "journal_body_bytes": size,
                "pending_final_batches": int(final["revision"]) - consumer.revision,
                "max_sampled_pending_revisions": max_backlog,
                "observed_page_highwater": page_highwater,
                "delivery_config": asdict(config),
            }
    finally:
        if consumer_task:
            consumer_task.cancel()
            await asyncio.gather(consumer_task, return_exceptions=True)
        server.should_exit = True
        await serving
        listener.close()
        delivery.read_page = original_page


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    args.runtime_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="navigation-delivery-measure-", dir=args.runtime_parent
    ) as tmp:
        root = Path(tmp)
        isolate(root)
        path = root / "planner.db"
        seed(path)
        warm = await measure_runtime(path, "warm", True)
        cold = [await measure_runtime(path, f"runtime-cold-{i}", False) for i in range(3)]
    return {
        "method": "loopback TCP HTTP/SSE; monotonic perf_counter_ns "
        "immediately after successful source commit return to receipt of complete "
        "blank-delimited SSE frame; runtime-cold restarts not OS-cache-cold or process-cold",
        "limitations": [
            "Synthetic 500 Tickets, 5000 settled turns, "
            "1000 unresolved attempts; no external backend",
            "Small sample: no production tail latency, capacity, or browser visibility claim",
            "Dedicated benchmark HTTP writer serializes source commit and revision read "
            "with a lock to correlate exact commits; write_ms includes admission wait",
            "Equivalent snapshot refresh is supporting backend transfer comparator only",
            "Periodic retention limits are targets; observed journal size is "
            "not a hard inter-interval ceiling",
            "Observed page highwater excludes framework/kernel socket "
            "buffers; configured send deadline bounds stalls",
        ],
        "warm": warm,
        "runtime_cold": cold,
        "browser_baseline_followup": [
            "Use original production browser/backend revision in a separate "
            "isolated worktree with its own dependencies and synthetic state",
            "Drive the same source mutations, pane selections, and offered "
            "schedule; preserve raw commit timestamps and browser query/render completion marks",
            "Record all request counts/bytes and collection+selector+render "
            "visibility at both revisions using browser tracing/HAR",
            "Compare 10 paired warm and 3 cold trials with identical dataset; "
            "require final visible values equality and unchanged pane/composition behavior",
            "Retain exact git tips, environment provenance, raw traces and "
            "samples; this script does not provide that baseline",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-parent", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(main_async(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        compact_json(
            {
                "output": str(args.output),
                "warm_samples": len(result["warm"]["samples"]),
                "runtime_cold_samples": len(result["runtime_cold"]),
                "exact_replay_equality": True,
            }
        )
    )


if __name__ == "__main__":
    main()
