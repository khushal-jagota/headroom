"""Record what the agent CLIs on the Mac do when the Mac sleeps in the middle of a turn.

Run from the VPS. One run is one cycle:

1. Start one Claude Code turn and one Codex turn on the Mac, outside Panels. A small
   recorder stamps every output line with the Mac's wall clock and monotonic clock.
2. Once both turns are producing events, put the Mac to sleep with ``pmset sleepnow``.
3. Wait for a full wake (not a maintenance "DarkWake"). A person wakes the Mac by hand.
4. Wait for both turns to end, then copy the records back and write a summary.

The run never touches Panels, its executor or any other process on the Mac. It stops
only the two CLI processes it started, by PID, if they are still running at the end.
Sleep pauses every running agent on the Mac, so agree each run with the owner first.

    python scripts/mac_sleep_test.py --minutes 15 --out ~/projects/panels/task-tmp/<id>/sleep
    python scripts/mac_sleep_test.py --no-sleep --out ...   # checks the recording only
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

MAC = "khushaljagota@100.118.172.84"
SSH = ("ssh", "-F", "/tmp/empty_ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6")
MAC_PATH = "$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin"

PROMPT = (
    "Run the shell command `date` 30 times. Make each run its own separate tool call, "
    "one after another, never in parallel. After each call, write the count on one "
    "short line. When all 30 are done, reply DONE."
)

# Runs on the Mac under its system python3 (3.9), so it must stay 3.9-compatible.
RECORDER = r"""
import json, os, subprocess, sys, threading, time

kind, run_dir, prompt = sys.argv[1], sys.argv[2], sys.argv[3]
os.chdir(run_dir)
home = os.path.expanduser("~")
extra = home + "/.local/bin:/opt/homebrew/bin:/usr/local/bin:"
os.environ["PATH"] = extra + os.environ.get("PATH", "/usr/bin:/bin")
# launchd restarts a submitted job when it exits. Run the turn once only, then idle
# until the test removes the job.
marker = os.path.join(run_dir, kind + ".started")
if os.path.exists(marker):
    time.sleep(3600)
    sys.exit(0)
open(marker, "w").close()
if kind == "claude":
    command = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--model", "haiku", "--no-session-persistence",
               "--allowedTools", "Bash(date)", "Bash(date:*)"]
else:
    command = ["codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
               "-s", "read-only", "-C", run_dir, prompt]
out = open(os.path.join(run_dir, kind + ".jsonl"), "a", buffering=1)
lock = threading.Lock()

def record(stream, line):
    with lock:
        out.write(json.dumps({"wall_ms": int(time.time() * 1000),
                              "mono_ms": int(time.monotonic() * 1000),
                              "stream": stream, "line": line}) + "\n")

process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, bufsize=1)
open(os.path.join(run_dir, kind + ".pid"), "w").write(str(process.pid))

def pump(stream, name):
    for line in stream:
        record(name, line.rstrip("\n"))

def tick():
    while process.poll() is None:
        record("tick", "")
        time.sleep(5)

threads = [threading.Thread(target=pump, args=(process.stdout, "stdout"), daemon=True),
           threading.Thread(target=pump, args=(process.stderr, "stderr"), daemon=True),
           threading.Thread(target=tick, daemon=True)]
for thread in threads:
    thread.start()
code = process.wait()
for thread in threads[:2]:
    thread.join(5)
record("exit", str(code))
"""

KINDS = ("claude", "codex")


def ssh(
    command: str, *, timeout: float = 30, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (*SSH, MAC, f"export PATH={MAC_PATH}; {command}"),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def label(run_id: str, kind: str) -> str:
    return f"com.panels.sleeptest.{run_id}.{kind}"


def reachable() -> bool:
    try:
        return ssh("true", timeout=12).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def log(message: str) -> None:
    print(f"{datetime.now(UTC):%H:%M:%S}Z {message}", flush=True)


def event_counts(run_dir: str) -> dict[str, int]:
    result = ssh(
        " ; ".join(
            f"printf '{kind} '; (grep -c '\"stream\": \"stdout\"' {run_dir}/{kind}.jsonl || true)"
            for kind in KINDS
        )
    )
    counts = {}
    for line in result.stdout.splitlines():
        kind, _, count = line.partition(" ")
        counts[kind] = int(count or 0)
    return counts


def ended(run_dir: str) -> dict[str, bool]:
    result = ssh(
        " ; ".join(
            f"printf '{kind} '; (grep -c '\"stream\": \"exit\"' {run_dir}/{kind}.jsonl || true)"
            for kind in KINDS
        )
    )
    return {
        line.partition(" ")[0]: line.partition(" ")[2].strip() not in ("", "0")
        for line in result.stdout.splitlines()
    }


def full_wake_after(started: datetime) -> str | None:
    """The first full Wake line in the Mac power log after the sleep began."""
    result = ssh("pmset -g log | grep -E ' (Sleep|Wake|DarkWake) ' | tail -40", timeout=40)
    for line in result.stdout.splitlines():
        match = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(\w+)\s", line)
        if not match or match.group(2) != "Wake":
            continue
        at = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S %z")
        if at > started:
            return line
    return None


def summarize(local_dir: Path, sleep_started_ms: int | None) -> dict[str, object]:
    summary: dict[str, object] = {}
    for kind in KINDS:
        path = local_dir / f"{kind}.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        output = [r for r in records if r["stream"] != "tick"]
        ticks = [r for r in records if r["stream"] == "tick"]
        gaps = [
            (b["wall_ms"] - a["wall_ms"], b["mono_ms"] - a["mono_ms"])
            for a, b in zip(ticks, ticks[1:], strict=False)
        ]
        largest = max(gaps, default=(0, 0))
        before = [r for r in output if sleep_started_ms is None or r["wall_ms"] < sleep_started_ms]
        after = [
            r for r in output if sleep_started_ms is not None and r["wall_ms"] >= sleep_started_ms
        ]
        exit_records = [r for r in records if r["stream"] == "exit"]
        summary[kind] = {
            "output_lines_before_sleep": len(before),
            "output_lines_after_sleep": len(after),
            "first_lines_after_sleep": [r["line"][:400] for r in after[:6]],
            "last_lines": [r["line"][:400] for r in output[-4:]],
            "stderr_lines": [r["line"][:400] for r in output if r["stream"] == "stderr"][-10:],
            "exit_code": exit_records[0]["line"] if exit_records else None,
            "largest_tick_gap_wall_s": round(largest[0] / 1000, 1),
            "largest_tick_gap_monotonic_s": round(largest[1] / 1000, 1),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=2, help="planned sleep length")
    parser.add_argument("--no-sleep", action="store_true", help="record the turns only")
    parser.add_argument("--out", type=Path, required=True, help="local folder for records")
    parser.add_argument("--end-wait-minutes", type=float, default=20)
    args = parser.parse_args()

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = f"/tmp/panels-sleep-test/{run_id}"
    local_dir = args.out / run_id
    local_dir.mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {"run_id": run_id, "planned_minutes": args.minutes}

    ssh(f"mkdir -p {run_dir} && cat > {run_dir}/recorder.py", stdin=RECORDER).check_returncode()
    # A job submitted to launchd runs in the logged-in session, where the CLIs can read
    # their stored credentials. A plain ssh session cannot, and Claude reports logged out.
    for kind in KINDS:
        ssh(
            f"launchctl submit -l {label(run_id, kind)} -o {run_dir}/{kind}.recorder.log "
            f"-e {run_dir}/{kind}.recorder.log -- /usr/bin/python3 {run_dir}/recorder.py "
            f"{kind} {run_dir} {shlex.quote(PROMPT)}"
        ).check_returncode()
    log(f"started both turns in {run_dir}")

    deadline = time.monotonic() + 180
    while True:
        counts = event_counts(run_dir)
        if all(counts.get(kind, 0) >= 4 for kind in KINDS):
            break
        if time.monotonic() > deadline:
            log(f"turns did not start producing events: {counts}")
            return 1
        time.sleep(3)
    log(f"both turns are producing events: {counts}")

    sleep_started_ms: int | None = None
    if not args.no_sleep:
        result = ssh(f"nohup sh -c 'sleep 2; pmset sleepnow' > {run_dir}/sleep.log 2>&1 &")
        result.check_returncode()
        started = datetime.now(UTC)
        sleep_started_ms = int(started.timestamp() * 1000) + 2000
        meta["sleep_requested_at"] = started.isoformat()
        log(f"asked the Mac to sleep; it needs a wake by hand after {args.minutes} minutes")
        time.sleep(30)
        wake_line = None
        while wake_line is None:
            time.sleep(15)
            if reachable():
                wake_line = full_wake_after(started)
        meta["full_wake"] = wake_line
        meta["reachable_again_at"] = datetime.now(UTC).isoformat()
        log(f"full wake: {wake_line}")

    deadline = time.monotonic() + args.end_wait_minutes * 60
    while not all(ended(run_dir).values()):
        if time.monotonic() > deadline:
            meta["still_running_after_minutes"] = args.end_wait_minutes
            ssh(f"for f in {run_dir}/*.pid; do kill $(cat $f) 2>/dev/null; done")
            log("a turn was still running; stopped the test's own CLI processes")
            time.sleep(10)
            break
        time.sleep(10)

    for kind in KINDS:
        ssh(f"launchctl remove {label(run_id, kind)}")
    for kind in KINDS:
        (local_dir / f"{kind}.jsonl").write_text(ssh(f"cat {run_dir}/{kind}.jsonl").stdout)
    window = ssh("pmset -g log | grep -E ' (Sleep|Wake|DarkWake) ' | tail -30", timeout=40)
    (local_dir / "pmset.log").write_text(window.stdout)
    meta["summary"] = summarize(local_dir, sleep_started_ms)
    (local_dir / "summary.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
