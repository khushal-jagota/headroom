"""Freeze and wake an isolated test executor many times, and prove no event is lost or
duplicated (contract §8.3, §11.10 row F).

Runs on the VPS only, fully isolated: its own temporary database, free ports, the test TLS
proxy and ``executor_id=test-executor``. It never touches the live server, the live
database or the Mac. It signals only the process group it starts.

    .venv/bin/python scripts/executor_sleep_cycles.py --cycles 100 --out DIR

Writes ``DIR/cycles.jsonl`` (one JSON object per cycle), ``DIR/summary.json`` and
``DIR/evidence/`` (logs and databases). Stops and exits non-zero on the first violation;
with ``--keep-going`` it runs every cycle, reports each violation once, and still exits
non-zero. The last log line gives the violation count and names the excused messages.
There is no wake hook yet: the sleep and wake rules come in wave 2.

Every user message must reach its agent exactly once, and every saved answer too. The
one excused loss, named and counted apart in ``summary.json``, is a message held in VPS
memory behind an open turn when the VPS restarted (a follow-up ticket owns it).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="run every cycle after a violation, to see them all",
    )
    args = parser.parse_args()
    # The harness starts its helper processes with ``python -m tests.support...``.
    sys.path.insert(0, str(REPOSITORY))
    os.chdir(REPOSITORY)
    from tests.support.executor_sleep_cycles import run_sleep_cycles

    async def run() -> int:
        task = asyncio.current_task()
        assert task is not None
        loop = asyncio.get_running_loop()
        # A stop request still runs the harness's teardown, which stops every process
        # it started.
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(number, task.cancel)
        reports = await run_sleep_cycles(
            args.out.resolve(), args.cycles, seed=args.seed, keep_going=args.keep_going
        )
        failed = len(reports) != args.cycles or any(r.violations for r in reports)
        return 1 if failed else 0

    try:
        return asyncio.run(run())
    except asyncio.CancelledError:
        print("stopped before the last cycle", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
