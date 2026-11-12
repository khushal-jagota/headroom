"""Three short sleep cycles of the 100-cycle harness (contract §8.3, §11.10 row F).

Risk: the harness itself rots (the real Mac half as its own process group, frozen and
woken, judged against its agents' durable logs), or a sleep, a Mac process restart or a
VPS restart during a Mac drain loses or duplicates an event, a message or an answer, runs
a command twice, or recovers an interrupted turn other than exactly once.
Gap: the 100-cycle run (scripts/executor_sleep_cycles.py) is run by hand on the VPS;
nothing else exercises these paths end to end on every change.
Layer: integration, the same harness and judge as the full run
(tests/support/executor_sleep_cycles.py) with a short schedule. Cycle 1 freezes,
dark-wakes, and leaves the requests the sleep held dead until the Mac's own timeouts.
Cycle 2 answers an ask while the Mac sleeps, kills the executor mid-turn while offline
before it can apply the answer, relaunches it on the same spool, and crashes the VPS
during its drain after it saved a batch it never acknowledged. Cycle 3 kills the executor
mid-upload, the mode that exposed the replayed-event fence bug. The VPS restart while the
same Mac process sleeps, and the freezes longer than every stall limit, run only in the
full run.
Cost: about 2.3 minutes of wall clock (measured in part-r-harness-notes.md).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.support.executor_sleep_cycles import CycleSchedule, run_sleep_cycles

SHORT = CycleSchedule(
    freeze_seconds=(4.0, 6.0),
    dark_wake_every=3,
    dark_wake_from=1,
    dark_wake_seconds=(1.5, 2.5),
    cut_every=0,
    mac_restart_every=1,
    mac_restart_from=2,
    vps_restart_every=2,
    vps_restart_asleep_every=0,
    long_freeze_every=0,
    stream_steps=30,
    stream_interval=0.1,
    final_quiet_seconds=15.0,
)


def test_three_sleep_cycles_lose_and_duplicate_nothing(tmp_path: Path) -> None:
    reports = asyncio.run(run_sleep_cycles(tmp_path / "out", 3, schedule=SHORT))

    assert [r.violations for r in reports] == [[], [], []]
    first, second, third = reports
    assert "dark_wake" in first.kinds and first.held_requests == "left_dead"
    assert "mac_restart" in second.kinds and second.vps_restart_during_drain is True
    assert second.kill_mode == "offline" and third.kill_mode == "mid_upload"
    for restart in (second, third):
        # Batches saved without the Mac seeing their receipts were sent again.
        assert restart.counts["items_resent"]
        # The kill caught a turn mid-stream, and that turn was interrupted and recovered
        # exactly once.
        assert restart.kill is not None
        recovered = [c for c in ("ca", "cb", "cc") if restart.counts[f"{c}_recoveries"]]
        assert recovered and all(restart.counts[f"{c}_interrupted"] == 1 for c in recovered)
    assert second.kill is not None and second.kill["outbox_waiting"]
    lines = (tmp_path / "out" / "cycles.jsonl").read_text().splitlines()
    assert [json.loads(line)["cycle"] for line in lines] == [1, 2, 3]
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["ok"] is True
