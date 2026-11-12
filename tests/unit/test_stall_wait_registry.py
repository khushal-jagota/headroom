"""The process-wide wait registry reports each wait once, from its first start.

Risk: two writers of one conversation track the same wait at once (one finding_key).
Two entries would let the later one hide how long the wait has really lasted, and an
ended wait would linger. The registry is new; E's checker tests take its snapshot as
given. Layer: the real registry with an injected clock.
"""

from __future__ import annotations

from planner.stalls.contracts import TrackedWaitKind
from planner.stalls.registry import ProcessWaitRegistry


def test_snapshot_keeps_one_wait_per_finding_key_from_its_earliest_start() -> None:
    times = iter((100, 200, 300))
    registry = ProcessWaitRegistry(clock=lambda: next(times))
    kind = TrackedWaitKind.vps_save_retrying
    with registry.track(kind, "c1", conversation_id="c1", detail="stuck at write 1") as first:
        with registry.track(kind, "c1", conversation_id="c1", detail="second writer"):
            with registry.track(kind, "c2", conversation_id="c2", detail="other"):
                first.set_detail("stuck at write 2")
                waits = {wait.finding_key: wait for wait in registry.snapshot()}
                assert len(registry.snapshot()) == 2
                assert waits["vps_save_retrying:c1"].started_at_ms == 100
                assert waits["vps_save_retrying:c1"].detail == "stuck at write 2"
                assert waits["vps_save_retrying:c2"].started_at_ms == 300
        assert [wait.started_at_ms for wait in registry.snapshot()] == [100]
    assert registry.snapshot() == ()
