"""`panels server-event-loop-tasks` reaches the loop of the application the supervisor runs."""

from __future__ import annotations

import json
import os
import subprocess

import httpx
from tests.support.server import PLAN_BIN, REPO_ROOT, ServerHandle


def test_event_loop_tasks_reads_the_supervised_application_loop(server: ServerHandle) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    env["PLAN_SERVER_CONTROL_SOCKET"] = str(server.control_socket_path)

    result = subprocess.run(
        [str(PLAN_BIN), "server-event-loop-tasks", "--json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20.0,
    )

    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["omitted_task_count"] == 0
    assert body["total_task_count"] == len(body["tasks"])
    # The application's serving task leads: the oldest, awaiting below its own serve().
    oldest = body["tasks"][0]
    assert oldest["coroutine"] == "ChangeStreamClosingServer.serve"
    assert len(oldest["stack"]) > 1
    assert oldest["age_seconds"] > 0
    assert httpx.get(f"{server.base}/api/meta").status_code == 200
