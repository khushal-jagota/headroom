"""Risk: service TERM bypasses Worker cleanup despite correct explicit stop commands.

Reuse the sleep-cycle host and real executor process. A unit test cannot prove OS signal
routing and supervised child termination across the daemon boundary.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from pathlib import Path

from tests.support.executor_https import serve_executor_app
from tests.support.executor_sleep_cycles import (
    CycleEndpoint,
    CycleSchedule,
    SleepCycles,
    _eventually,
    _make_core,
    read_records,
)

from planner.platform_runtime.linux.worker_processes import LinuxWorkerProcesses


def test_linux_executor_term_stops_active_supervised_worker(tmp_path: Path) -> None:
    class OneWorker(SleepCycles):
        conversations = ("ca",)
        roles = ("stream",)

    async def exercise() -> None:
        harness = OneWorker(
            tmp_path / "work", tmp_path / "out", CycleSchedule(), 1, keep_going=False
        )
        for folder in (
            harness.work / "vps",
            harness.mac_root,
            harness.agent_logs,
            harness.logs,
            harness.out,
        ):
            folder.mkdir(parents=True)
        harness._create_database()
        harness.endpoint = CycleEndpoint(harness.db_path, harness.ledger)
        harness.system = _make_core(harness.endpoint)
        await harness.endpoint.start()
        harness._build_transport_app()
        try:
            async with serve_executor_app(harness.transport_app, harness.work / "tls") as proxy:
                harness.proxy = proxy
                try:
                    await harness._launch_executor(None)
                    await harness._start_conversations()
                    harness._send(1, "ca", "msg[term.active] steps=100 interval=0.2")

                    async def active() -> bool:
                        return any(
                            event.get("t") == "emit" and event.get("turn") == 2
                            for event in read_records(harness.agent_logs / "ca.jsonl")
                        )

                    await _eventually(active, "Worker did not start its active turn", 15)
                    owned = harness._owned_agents()
                    assert owned
                    assert harness.process is not None
                    started = time.monotonic()
                    harness.process.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(asyncio.to_thread(harness.process.wait), 20)
                    assert harness.process.returncode == 0
                    primitives = LinuxWorkerProcesses()
                    assert all(
                        primitives.is_absent(process, time.monotonic() + 2) for process in owned
                    )
                    records = [
                        json.loads(path.read_text())
                        for path in (harness.mac_root / "run/worker-processes").glob("*.json")
                    ]
                    assert records and all(record["state"] == "complete" for record in records)
                    print(
                        "Linux daemon TERM active Worker cleanup: "
                        f"{time.monotonic() - started:.3f}s"
                    )
                finally:
                    await harness._stop_everything()
        finally:
            harness._keep_evidence()

    asyncio.run(exercise())
