"""Source-bound strict Bubblewrap admission and process-cost probe.

Run on the hosted runner after dependencies exist. The evidence directory must
be absent. Local execution does not establish hosted admission.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from test_containment.runner import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    options = parser.parse_args()
    roots = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    visible_roots = [fields for fields in roots if fields[4] == "/"]
    if not visible_roots or any(
        fields[fields.index("-") + 1] != "tmpfs" for fields in visible_roots
    ):
        parser.error("run this probe through verification.py --tool probe")
    sentinel = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".codex/skills/sentinel.txt"
    if sentinel.read_text() != "protected disposable sentinel\n":
        parser.error("the disposable outer account sentinel is unavailable")
    if options.evidence.exists() or not 1 <= options.samples <= 30:
        parser.error("use an absent evidence directory and 1 to 30 samples")
    options.evidence.mkdir(mode=0o700)
    durations: list[float] = []
    baseline: list[float] = []
    pytest_baseline: list[float] = []
    stages: list[dict[str, float]] = []
    status = 0
    for index in range(options.samples):
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "pass"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            close_fds=True,
            check=True,
        )
        baseline.append(time.perf_counter() - start)
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            close_fds=True,
            check=True,
        )
        pytest_baseline.append(time.perf_counter() - start)
        start = time.perf_counter()
        child_status = run(["--version"], options.evidence / f"sample-{index}")
        durations.append(time.perf_counter() - start)
        supervisor = json.loads((options.evidence / f"sample-{index}/supervisor.json").read_text())
        admission_path = options.evidence / f"sample-{index}/reports/admission.json"
        if admission_path.exists():
            admission = json.loads(admission_path.read_text())
            stages.append(
                {
                    "boundary_preparation_seconds": supervisor["boundary_prepare_seconds"],
                    "namespace_and_python_start_seconds": admission["entered_monotonic"]
                    - supervisor["child_started_monotonic"],
                    "admission_seconds": admission["seconds"],
                }
            )
        status = status or child_status
        if child_status:
            break
    (options.evidence / "timing.json").write_text(
        json.dumps(
            {
                "status": status,
                "strict_process_seconds": durations,
                "plain_python_seconds": baseline,
                "plain_pytest_version_seconds": pytest_baseline,
                "paired_added_seconds": [
                    a - b for a, b in zip(durations, pytest_baseline, strict=True)
                ],
                "median_added_seconds": statistics.median(
                    a - b for a, b in zip(durations, pytest_baseline, strict=True)
                ),
                "stages": stages,
                "median_strict_process_seconds": statistics.median(durations),
                "median_plain_python_seconds": statistics.median(baseline),
                "scope": "namespace setup + admission + pytest startup; excludes suite execution",
                "hosted_result": "Result applies only to the machine that executes this command.",
            },
            indent=2,
        )
    )
    return status


if __name__ == "__main__":
    raise SystemExit(main())
