"""Agent process priority and the orphan cleanup at executor start.

Risk: a dead executor's agents keep running and writing after the VPS has treated them
as gone, or the cleanup signals a process it never recorded (a reused PID). Layer: real
processes this test starts itself, real `ps`, `pgrep` and signals; nothing else is
touched, because the cleanup acts only on PIDs in its own file whose start time matches.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from planner.executor.configuration import AGENT_CHILD_NICE
from planner.executor.mac_agent_processes import (
    AGENT_PROCESSES_NAME,
    AgentProcesses,
    process_start_time,
)

# A parent standing in for the old executor: it starts one agent that ends on SIGTERM
# and one that ignores it, prints their PIDs once both are ready, and exits when told.
PARENT = """
import subprocess, sys
agents = [
    subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    for code in (
        "import time; print('ready', flush=True); time.sleep(60)",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)",
    )
]
for agent in agents:
    agent.stdout.readline()
print(" ".join(str(agent.pid) for agent in agents), flush=True)
sys.stdin.read()
"""


def wait_gone(process_id: int, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while process_start_time(process_id) is not None:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_cleanup_ends_recorded_agents_that_still_match_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "planner.executor.mac_agent_processes.ORPHAN_AGENT_TERMINATION_GRACE_SECONDS", 1.0
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    agents: list[int] = []
    started_at: dict[int, str | None] = {}
    try:
        assert parent.stdout is not None
        agents = [int(pid) for pid in parent.stdout.readline().split()]
        started_at.update({pid: process_start_time(pid) for pid in agents})
        old_executor = AgentProcesses(tmp_path / "run", parent.pid)
        old_executor.record_children()
        recorded = json.loads((tmp_path / "run" / AGENT_PROCESSES_NAME).read_text())
        assert sorted(item["process_id"] for item in recorded["processes"]) == sorted(agents)
        assert all(os.getpriority(os.PRIO_PROCESS, pid) >= AGENT_CHILD_NICE for pid in agents)
        # A recorded PID whose start time no longer matches was reused: never signalled.
        recorded["processes"].append({"process_id": decoy.pid, "started": "Thu Jan  1 1970"})
        (tmp_path / "run" / AGENT_PROCESSES_NAME).write_text(json.dumps(recorded))
        # The old executor dies; its agents do not.
        assert parent.stdin is not None
        parent.stdin.close()
        parent.wait(timeout=10)
        assert all(process_start_time(pid) is not None for pid in agents)

        started = time.monotonic()
        AgentProcesses(tmp_path / "run", os.getpid()).end_orphans()
        assert time.monotonic() - started >= 1.0  # The SIGTERM-proof agent needed SIGKILL.
        assert all(wait_gone(pid) for pid in agents)
        assert decoy.poll() is None
        assert json.loads((tmp_path / "run" / AGENT_PROCESSES_NAME).read_text()) == {
            "processes": []
        }
    finally:
        for pid in agents:  # Only the agents this test started, if a check failed early.
            if process_start_time(pid) == started_at.get(pid):
                os.kill(pid, signal.SIGKILL)
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        decoy.kill()
        decoy.wait()


def test_cleanup_also_ends_the_tools_an_agent_started_before_and_after_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: only the agent CLI is ended, so the builds, servers and shells it started
    keep running in the workspace after the VPS has treated that agent as gone."""
    monkeypatch.setattr(
        "planner.executor.mac_agent_processes.ORPHAN_AGENT_TERMINATION_GRACE_SECONDS", 1.0
    )
    agent_script = (
        f"sleep 60 & echo $! > {tmp_path}/early; "
        f"while [ ! -e {tmp_path}/go ]; do sleep 0.05; done; "
        f"sleep 60 & echo $! > {tmp_path}/late; wait"
    )
    # The old executor's stand-in: it starts the agent and exits when told.
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys; agent = subprocess.Popen(['/bin/sh', '-c', sys.argv[1]]); "
            "print(agent.pid, flush=True); sys.stdin.read()",
            agent_script,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    started_at: dict[int, str | None] = {}

    def pid_in(name: str) -> int:
        deadline = time.monotonic() + 10
        while not (tmp_path / name).exists() or not (tmp_path / name).read_text().strip():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        process_id = int((tmp_path / name).read_text())
        started_at[process_id] = process_start_time(process_id)
        return process_id

    try:
        assert parent.stdout is not None
        agent = int(parent.stdout.readline())
        started_at[agent] = process_start_time(agent)
        early = pid_in("early")
        AgentProcesses(tmp_path / "run", parent.pid).record_children()
        (tmp_path / "go").touch()
        late = pid_in("late")  # Started after the last record, while its agent runs.
        assert parent.stdin is not None
        parent.stdin.close()
        parent.wait(timeout=10)
        AgentProcesses(tmp_path / "run", os.getpid()).end_orphans()
        assert all(wait_gone(pid) for pid in (agent, early, late))
    finally:
        for pid, started in started_at.items():  # Only what this test started.
            if started is not None and process_start_time(pid) == started:
                os.kill(pid, signal.SIGKILL)
        if parent.poll() is None:
            parent.kill()
            parent.wait()
