"""Minimal namespace child. The admission checker validates actual kernel state."""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    for name in ("tmp", "cache", "data", "reports"):
        (options.scratch / name).mkdir(exist_ok=True)
    for key in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    # Bubblewrap does not promise PR_SET_NO_NEW_PRIVS independently of seccomp.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):
        raise RuntimeError("cannot set no-new-privileges")
    from test_containment.admission import require_containment

    started = time.perf_counter()
    require_containment()
    (options.scratch / "reports/admission.json").write_text(
        json.dumps({"seconds": time.perf_counter() - started, "entered_monotonic": started})
    )
    try:
        args = options.args[1:] if options.args[:1] == ["--"] else options.args
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                f"cache_dir={options.scratch}/cache/pytest",
                *args,
            ],
            close_fds=True,
        )
    finally:
        reports = {}
        for path in (options.scratch / "reports").rglob("*"):
            if path.is_file() and not path.is_symlink():
                reports[str(path.relative_to(options.scratch / "reports"))] = base64.b64encode(
                    path.read_bytes()
                ).decode()
        print(
            "\nPANELS_CONTAINMENT_REPORTS_V1:"
            + base64.b64encode(json.dumps(reports).encode()).decode(),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
