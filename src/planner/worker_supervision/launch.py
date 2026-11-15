"""Worker bootstrap: persist exact custody and priority before backend execution."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from planner.platform_runtime.selection import get_platform_adapter
from planner.worker_supervision import _validate_pending_shape, config, records


def _launch(path: Path, arguments: list[str]) -> None:
    record = records.read(path)
    _validate_pending_shape(record)
    if record.get("launch_revoked") or record.get("state") == "complete":
        raise RuntimeError("Worker launch authorization was revoked before backend execution")
    try:
        primitives = get_platform_adapter().worker_processes()
        # Identity exists before priority or exec can fail. Session evidence follows setsid.
        deadline = time.monotonic() + config.INSPECTION_SECONDS
        process = primitives.snapshot(deadline)[os.getpid()]
        record.update(
            root=asdict(process),
            processes=[asdict(process)],
            launch_phase="registered",
            state="registered",
        )
        records.write(path, record)
        primitives.prepare_launch()
        process = primitives.snapshot(deadline)[os.getpid()]
        record.update(
            root=asdict(process),
            processes=[asdict(process)],
            isolated=True,
            launch_phase="running",
            state="running",
        )
        records.write(path, record)
    except BaseException as error:
        record.update(state="bootstrap_failed", reason=f"Worker launch failed: {error}")
        records.write(path, record)
        raise


def main() -> None:
    path = Path(sys.argv[1])
    arguments = sys.argv[2:]
    with records.locked(path, time.monotonic() + config.START_SECONDS):
        _launch(path, arguments)
    os.execvpe(arguments[0], arguments, os.environ)


if __name__ == "__main__":
    main()
