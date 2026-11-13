"""Short isolated one-versus-ten executor comparison. Never uses live data."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import cast

from tests.support.multi_machine_home import Executor, Home, clean_environment, wait_for


def source_identity() -> dict[str, str]:
    digest = hashlib.sha256()
    for path in sorted(Path("src/planner").rglob("*.py")):
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "runtime_source_sha256": digest.hexdigest(),
    }


async def sample(root: Path, count: int, seconds: float) -> dict[str, object]:
    source_before = source_identity()
    async with Home(root / "h", count) as home:
        before = await home.request("/proof/metrics")
        started = time.monotonic()
        async with contextlib.AsyncExitStack() as stack:
            clients = [
                await stack.enter_async_context(Executor(root / f"e{i}", home.proxy, grant))
                for i, grant in enumerate(home.grants)
            ]
            deliveries = await asyncio.gather(
                *(
                    home.request(f"/proof/start/{grant.machine_id}/load{i}", post=True)
                    for i, grant in enumerate(home.grants)
                )
            )
            assert all(d["delivery"] == "PromptDeliveryStarted" for d in deliveries), deliveries
            await wait_for(lambda: all(c.native.prompt_count == 1 for c in clients), "load prompts")
            await asyncio.gather(*(c.native.end_turn() for c in clients))
            while True:
                state = await home.request("/proof/state")
                if len(state["ended"]) == count and state["open_commands"] == 0:
                    break
                assert time.monotonic() - started < seconds + 15, state
                await asyncio.sleep(0.05)
            await asyncio.sleep(max(0, seconds - (time.monotonic() - started)))
            after = await home.request("/proof/metrics")
            wall = after["wall_monotonic"] - before["wall_monotonic"]
            cpu = after["cpu_seconds"] - before["cpu_seconds"]
            result = {
                "executors": count,
                "home_pid": after["home_pid"],
                "wall_seconds": wall,
                "home_cpu_seconds": cpu,
                "home_cpu_percent_one_core": 100 * cpu / wall,
                "physical_database_opens": after.get("physical_database_opens", 0)
                - before.get("physical_database_opens", 0),
                "fresh_connection_api_calls": after.get("fresh_connection_api_calls", 0)
                - before.get("fresh_connection_api_calls", 0),
                "runtime_connection_loans": after.get("runtime_connection_loans", 0)
                - before.get("runtime_connection_loans", 0),
                "write_transactions": after.get("write_transactions", 0)
                - before.get("write_transactions", 0),
                "row_changes": after.get("row_changes", 0) - before.get("row_changes", 0),
                "database_locked_errors": after.get("database_locked_errors", 0)
                - before.get("database_locked_errors", 0),
                "logged_database_locked_errors": after.get("logged_database_locked_errors", 0)
                - before.get("logged_database_locked_errors", 0),
                "lease_reply_bytes": {
                    grant.machine_id: c.lease_bytes
                    for grant, c in zip(home.grants, clients, strict=True)
                },
                "completed_turns": state["ended"],
            }
        log = (root / "h" / "home.log").read_text()
        result["home_log_database_locked_occurrences"] = log.lower().count("database is locked")
        result["source_before"] = source_before
        result["source_after"] = source_identity()
        result["source_unchanged"] = result["source_before"] == result["source_after"]
        return result


async def run(output: Path, seconds: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "source.diff").write_bytes(subprocess.check_output(["git", "diff", "--binary"]))
    base = Path("/home/vps/rfnd")
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="load-", dir=base) as folder:
        root = Path(folder)
        env = clean_environment(root)
        os.environ.clear()
        os.environ.update(env)
        results = []
        for count in (1, 10):
            result = await sample(root / str(count), count, seconds)
            results.append(result)
            (output / f"home-{count}.log").write_bytes(
                (root / str(count) / "h" / "home.log").read_bytes()
            )
        cpu_per_turn = [
            cast(float, r["home_cpu_seconds"]) / len(cast(list[object], r["completed_turns"]))
            for r in results
        ]
        report = {
            "sample_seconds": seconds,
            "workload": (
                "One real start, prompt, and completed turn per executor, "
                "then idle leases/polls to the same sample duration."
            ),
            "database_metric": (
                "Successful mutating commits and total_changes deltas at those commits. "
                "Includes triggers. SQLite total_changes does not subtract savepoint rollbacks. "
                "Not disk IO or WAL bytes."
            ),
            "cpu_metric": (
                "time.process_time of isolated home PID; "
                "excludes executor/proxy CPU; percent of one core."
            ),
            "cpu_seconds_per_completed_turn": cpu_per_turn,
            "ten_to_one_cpu_per_turn_ratio": cpu_per_turn[1] / cpu_per_turn[0],
            "results": results,
        }
        (output / "load.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=12)
    args = parser.parse_args()
    asyncio.run(run(args.output.resolve(), args.seconds))


def test_load_comparison() -> None:
    """Explicit guarded entry: scripts/dev pytest scripts/multi_executor_load.py -s."""
    asyncio.run(run(Path(os.environ["MULTI_EXECUTOR_PROOF_OUTPUT"]).resolve(), 12))


def test_profile_ten() -> None:
    """Explicit short profile of the same useful ten-executor workload."""

    async def profile() -> None:
        output = Path(os.environ["MULTI_EXECUTOR_PROOF_OUTPUT"]).resolve()
        with tempfile.TemporaryDirectory(prefix="profile-", dir="/home/vps/rfnd") as folder:
            root = Path(folder)
            environment = clean_environment(root)
            environment["MULTI_HOME_PROFILE"] = "1"
            os.environ.clear()
            os.environ.update(environment)
            result = await sample(root, 10, 8)
            (output / "profile-metrics.json").write_text(json.dumps(result, indent=2) + "\n")
            (output / "profile.json").write_bytes((root / "h" / "profile.json").read_bytes())
            (output / "profile-home.log").write_bytes((root / "h" / "home.log").read_bytes())
            print(json.dumps(result, indent=2))

    asyncio.run(profile())


def test_ten_only() -> None:
    """Explicit fixed-workload ten-executor diagnostic for a controlled source pair."""

    async def measure() -> None:
        output = Path(os.environ["MULTI_EXECUTOR_PROOF_OUTPUT"]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        (output / "source.diff").write_bytes(subprocess.check_output(["git", "diff", "--binary"]))
        with tempfile.TemporaryDirectory(prefix="ten-", dir="/home/vps/rfnd") as folder:
            root = Path(folder)
            environment = clean_environment(root)
            os.environ.clear()
            os.environ.update(environment)
            result = await sample(root, 10, 12)
            (output / "load.json").write_text(json.dumps(result, indent=2) + "\n")
            (output / "home.log").write_bytes((root / "h" / "home.log").read_bytes())
            print(json.dumps(result, indent=2))

    asyncio.run(measure())
