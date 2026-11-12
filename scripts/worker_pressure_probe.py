#!/usr/bin/env python3
"""Ticket pressure evidence on a disposable GitHub-hosted Linux runner only."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def hosted_only() -> None:
    if (
        sys.platform != "linux"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise SystemExit("Refused: this probe requires a disposable GitHub-hosted Linux runner")


def write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def phase(root: Path, value: str) -> None:
    write(root / "phase.json", {"phase": value, "time": time.time()})


def process_evidence(pid: int) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": pid}
    for name in ("oom_score_adj", "cgroup", "comm"):
        try:
            result[name] = Path(f"/proc/{pid}/{name}").read_text().strip()
        except OSError as error:
            result[name] = str(error)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        result["parent"] = int(fields[1])
    except (OSError, ValueError, IndexError):
        result["parent"] = None
    try:
        result["nice"] = os.getpriority(os.PRIO_PROCESS, pid)
    except ProcessLookupError:
        result["nice"] = None
    return result


def home_processes(launcher: int) -> list[dict[str, Any]]:
    """Identify the actual app below its known launcher, without command arguments."""
    group = Path("/proc/self/cgroup").read_text().split("0::", 1)[1].strip()
    pids = (Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.procs").read_text()
    processes = [process_evidence(int(pid)) for pid in pids.split()]
    owned = {launcher}
    while children := {p["pid"] for p in processes if p.get("parent") in owned} - owned:
        owned.update(children)
    return [p for p in processes if p["pid"] in owned]


def health(port: int, sha: str) -> dict[str, Any]:
    start = time.monotonic()
    result: dict[str, Any] = {"time": time.time(), "ready": False}
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health?expected_sha={sha}",
            timeout=2,
        ) as response:
            result.update(status=response.status, body=json.load(response))
            result["ready"] = response.status == 200 and result["body"].get("ready") is True
    except (OSError, ValueError, urllib.error.URLError) as error:
        result["error"] = str(error)
    result["seconds"] = time.monotonic() - start
    return result


def memory_events(root: Path, tag: str) -> None:
    group = Path("/proc/self/cgroup").read_text().split("0::", 1)[1].strip()
    directory = Path("/sys/fs/cgroup") / group.lstrip("/")
    values = {}
    for name in ("memory.events", "memory.peak", "memory.current", "memory.max", "memory.swap.max"):
        try:
            values[name] = (directory / name).read_text()
        except OSError as error:
            values[name] = str(error)
    write(root / f"memory-{tag}.json", values)


async def controller(root: Path, port: int, sha: str) -> int:
    from planner.conversation.worker_scratch import WorkerScratch
    from planner.worker_supervision import WorkerSupervisor

    checkout = Path(__file__).resolve().parent.parent
    home = root / "home"
    home.mkdir()
    state = root / "state"
    state.mkdir()
    (root / "workspace").mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env.update(
        {
            "HOME": str(home),
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
            "PLAN_DB_PATH": str(state / "planner.db"),
            "PLAN_LOGS_DIR": str(state / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(state / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(state / "control.sock"),
            "PLAN_HERMES_HOME": str(home / ".hermes"),
            "PLAN_PORT": str(port),
            "PLAN_APP_SHA": sha,
            "PLAN_BACKUP_DIR": str(state / "backups"),
            "PLAN_WORKER_PROCESS_ROOT": str(root / "worker-processes"),
            "PLAN_CONFIG_PATH": str(root / "absent-config.yaml"),
            "PLAN_WORKSPACE_ROOT": str(root / "workspace"),
            "TMPDIR": str(root / "temp"),
        }
    )
    Path(env["TMPDIR"]).mkdir()
    os.environ.update(
        {"HOME": str(home), "PLAN_WORKER_PROCESS_ROOT": env["PLAN_WORKER_PROCESS_ROOT"]}
    )
    phase(root, "home-start")
    results: dict[str, Any] = {
        "source": sha,
        "controller": process_evidence(os.getpid()),
        "workers": [],
    }
    memory_events(root, "before")
    with (root / "home.log").open("wb") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "planner", "serve"],
            env=env,
            cwd=checkout,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                if health(port, sha)["ready"]:
                    break
                if server.poll() is not None:
                    raise RuntimeError(f"Home startup exited: {server.returncode}")
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Home did not become ready")
            # The launcher may own an app child. Preserve all same-service PID scores.
            results["home_launcher"] = process_evidence(server.pid)
            results["home_processes"] = home_processes(server.pid)
            if len(results["home_processes"]) < 2:
                raise RuntimeError("The actual home application process was not identified")
            if any(p.get("oom_score_adj") != "-500" for p in results["home_processes"]):
                raise RuntimeError("The home did not inherit its protected OOM score")
            write(root / "results.json", results)
            phase(root, "baseline")
            await asyncio.sleep(3)
            for name, argv, timeout in (
                ("heavy-build", ["npm", "--prefix", "web", "run", "build"], 240),
                (
                    "low-memory",
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "memory-worker",
                        "--root",
                        str(root),
                    ],
                    90,
                ),
            ):
                phase(root, name)
                worker_env = {**env, "PLAN_TICKET_ID": f"pressure-{name}"}
                supervisor = await WorkerSupervisor.create(worker_env, checkout)
                assert supervisor is not None
                scratch = WorkerScratch(worker_env)
                supervisor.environment.update(scratch.environment)
                begin = time.monotonic()
                outcome: dict[str, Any] = {
                    "phase": name,
                    "argv": argv,
                    "record": str(supervisor.path),
                }
                with (root / f"{name}.log").open("wb") as output:
                    child = await asyncio.create_subprocess_exec(
                        *supervisor.argv(argv),
                        env=supervisor.environment,
                        cwd=checkout,
                        stdout=output,
                        stderr=output,
                    )
                    await supervisor.started(child.pid)
                    outcome["process"] = process_evidence(child.pid)
                    if int(outcome["process"]["oom_score_adj"]) < 0:
                        raise RuntimeError("A Worker retained protected OOM priority")
                    if outcome["process"]["cgroup"] != results["controller"]["cgroup"]:
                        raise RuntimeError("A Worker escaped the shared home resource domain")
                    try:
                        await asyncio.wait_for(child.wait(), timeout)
                    except TimeoutError:
                        outcome["timed_out"] = True
                    outcome["returncode"] = child.returncode
                    outcome["elapsed_seconds"] = time.monotonic() - begin
                    stop_started = time.monotonic()
                    cleanup = await supervisor.stop()
                    outcome["stop_seconds"] = time.monotonic() - stop_started
                    outcome["cleanup"] = {"complete": cleanup.complete, "reason": cleanup.reason}
                    if cleanup.complete:
                        await asyncio.wait_for(child.wait(), 2)
                        outcome["returncode_after_cleanup"] = child.returncode
                        scratch.close()
                    results["workers"].append(outcome)
                    write(root / "results.json", results)
                memory_events(root, name)
                phase(root, f"after-{name}")
                await asyncio.sleep(5)
            phase(root, "final-health")
            await asyncio.sleep(5)
            results["final_health"] = health(port, sha)
            results["home_processes_after"] = home_processes(server.pid)
            results["home_returncode_before_stop"] = server.poll()
            memory_events(root, "after")
            write(root / "results.json", results)
            return 0
        except Exception as error:
            results["error"] = repr(error)
            write(root / "results.json", results)
            return 1
        finally:
            phase(root, "home-stop")
            if server.poll() is None:
                server.send_signal(signal.SIGTERM)
                try:
                    server.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            phase(root, "done")


def allocate(root: Path) -> int:
    write(root / "allocator-process.json", process_evidence(os.getpid()))
    # Permit root registration and the external health monitor to observe this tool.
    time.sleep(2)
    chunks = []
    while len(chunks) < 2048:
        chunks.append(bytearray(1024 * 1024))
        time.sleep(0.005)
    time.sleep(10)
    return 0


def outer(root: Path) -> int:
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    checkout = Path(__file__).resolve().parent.parent
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    unit = (
        f"panels-worker-pressure-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    )
    write(
        root / "probe.json",
        {
            "source": sha,
            "unit": unit,
            "port": port,
            "limits": {"MemoryMax": "1G", "MemorySwapMax": 0},
        },
    )
    command = [
        "sudo",
        "systemd-run",
        f"--unit={unit}",
        f"--uid={os.getuid()}",
        f"--gid={os.getgid()}",
        f"--working-directory={checkout}",
        "--property=MemoryMax=1G",
        "--property=MemorySwapMax=0",
        "--property=MemoryLow=1G",
        "--property=CPUWeight=100",
        "--property=OOMPolicy=continue",
        "--property=OOMScoreAdjust=-500",
        "--property=NoNewPrivileges=yes",
        "--property=PrivateTmp=yes",
        "--property=RuntimeMaxSec=480",
        "--property=TimeoutStopSec=20",
        "--setenv=GITHUB_ACTIONS=true",
        "--setenv=RUNNER_ENVIRONMENT=github-hosted",
        f"--setenv=PATH={os.environ['PATH']}",
        sys.executable,
        str(Path(__file__).resolve()),
        "controller",
        "--root",
        str(root),
        "--port",
        str(port),
        "--sha",
        sha,
    ]
    start = time.monotonic()
    samples = []
    try:
        subprocess.run(command, check=True, timeout=30)
        with (root / "health.jsonl").open("w") as log:
            while time.monotonic() - start < 500:
                current = (
                    json.loads((root / "phase.json").read_text())
                    if (root / "phase.json").exists()
                    else {"phase": "service-start"}
                )
                sample = {**health(port, sha), "phase": current["phase"]}
                samples.append(sample)
                log.write(json.dumps(sample) + "\n")
                log.flush()
                if current["phase"] == "done":
                    break
                state = subprocess.check_output(
                    ["sudo", "systemctl", "show", unit, "--property=ActiveState", "--value"],
                    text=True,
                    timeout=5,
                ).strip()
                if state in {"failed", "inactive"}:
                    break
                time.sleep(0.5)
        evidence = subprocess.run(
            ["sudo", "systemctl", "show", unit], capture_output=True, text=True, timeout=10
        )
        (root / "service-properties.txt").write_text(evidence.stdout + evidence.stderr)
        journal = subprocess.run(
            ["sudo", "journalctl", "--unit", unit, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        (root / "service-journal.txt").write_text(journal.stdout + journal.stderr)
        active = [
            s
            for s in samples
            if s["phase"]
            in {
                "heavy-build",
                "low-memory",
                "after-heavy-build",
                "after-low-memory",
                "final-health",
            }
        ]
        final = [s for s in active if s["phase"] == "final-health"]
        results = (
            json.loads((root / "results.json").read_text())
            if (root / "results.json").exists()
            else {}
        )
        usable = (
            bool(final)
            and any(s["ready"] for s in final)
            and results.get("final_health", {}).get("ready") is True
        )
        before = (
            json.loads((root / "memory-before.json").read_text())
            if (root / "memory-before.json").exists()
            else {}
        )
        after = (
            json.loads((root / "memory-after.json").read_text())
            if (root / "memory-after.json").exists()
            else {}
        )

        def oom_kills(evidence: dict[str, Any]) -> int:
            for line in evidence.get("memory.events", "").splitlines():
                if line.startswith("oom_kill "):
                    return int(line.split()[1])
            return 0

        oom_kill_delta = oom_kills(after) - oom_kills(before)
        allocator = (
            json.loads((root / "allocator-process.json").read_text())
            if (root / "allocator-process.json").exists()
            else {}
        )
        workers = results.get("workers", [])
        probe_complete = (
            len(workers) == 2
            and {worker["phase"] for worker in workers} == {"heavy-build", "low-memory"}
            and all(worker.get("cleanup", {}).get("complete") for worker in workers)
            and oom_kill_delta > 0
            and allocator.get("oom_score_adj") is not None
            and int(allocator["oom_score_adj"]) >= 0
            and allocator.get("cgroup") == results.get("controller", {}).get("cgroup")
        )
        report = {
            "probe_complete": probe_complete,
            "oom_kill_delta": oom_kill_delta,
            "allocator": allocator,
            "worker_outcomes": workers,
            "elapsed_seconds": time.monotonic() - start,
            "samples_during_load": len(active),
            "outages_during_load": sum(not s["ready"] for s in active),
            "max_latency_seconds": max((s["seconds"] for s in active), default=None),
            "home_usable_after_pressure": usable,
            "controller_error": results.get("error"),
            "note": "Any outage is reportable. Only an unusable home blocks this Ticket.",
        }
        write(root / "summary.json", report)
        print(json.dumps(report, indent=2))
        return 0 if usable and probe_complete and not results.get("error") else 1
    finally:
        # This exact disposable harness service is the only unit this probe can stop.
        subprocess.run(["sudo", "systemctl", "stop", unit], check=False, timeout=30)


def main() -> int:
    hosted_only()  # Before directory creation, subprocesses, allocation, or signals.
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("probe", "controller", "allocate", "memory-worker"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--sha", default="")
    args = parser.parse_args()
    if args.mode == "probe":
        return outer(args.root)
    if args.mode == "allocate":
        return allocate(args.root)
    if args.mode == "memory-worker":
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "allocate", "--root", str(args.root)],
            check=False,
        ).returncode
    return asyncio.run(controller(args.root, args.port, args.sha))


if __name__ == "__main__":
    raise SystemExit(main())
