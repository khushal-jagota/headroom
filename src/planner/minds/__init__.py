"""minds — the agent-operation primitive (W1).

Drives one Hermes "mind" (a durable gateway session) over the stdio JSON-RPC
gateway: spawn a child per run, create/resume the session, submit one prompt,
observe the single message.complete. Not wired into the server, dispatcher,
DB, or CLI this wave.
"""

from __future__ import annotations

from planner.minds.config import (
    boot_smoke_check,
    hermes_src_root,
    resolve_hermes_python,
    resolve_planner_home,
)
from planner.minds.gateway import (
    ChildProcess,
    GatewayChild,
    GatewayError,
    GatewayRpcError,
    SpawnFn,
    spawn_popen,
)
from planner.minds.queue import MindQueue
from planner.minds.runner import RunResult, RunStatus, run_step

__all__ = [
    "ChildProcess",
    "GatewayChild",
    "GatewayError",
    "GatewayRpcError",
    "MindQueue",
    "RunResult",
    "RunStatus",
    "SpawnFn",
    "boot_smoke_check",
    "hermes_src_root",
    "resolve_hermes_python",
    "resolve_planner_home",
    "run_step",
    "spawn_popen",
]
