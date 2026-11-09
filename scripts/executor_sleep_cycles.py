"""Freeze and wake an isolated test executor many times, and prove no event is lost or
duplicated (contract §8.3, §11.10 row F).

Runs on the VPS only, fully isolated: its own temporary database, free ports, the test TLS
proxy and ``executor_id=test-executor``. It never touches the live server, the live
database or the Mac. It signals only the process group it starts.

    .venv/bin/python scripts/executor_sleep_cycles.py --cycles 100 --out DIR

Writes ``DIR/cycles.jsonl`` (one JSON object per cycle), ``DIR/summary.json`` and
``DIR/evidence/`` (logs and databases). Exits non-zero on the first violation.
There is no wake hook yet: the sleep and wake rules come in wave 2.
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
        reports = await run_sleep_cycles(args.out.resolve(), args.cycles, seed=args.seed)
        failed = len(reports) != args.cycles or any(r.violations for r in reports)
        return 1 if failed else 0

    try:
        return asyncio.run(run())
    except asyncio.CancelledError:
        print("stopped before the last cycle", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
