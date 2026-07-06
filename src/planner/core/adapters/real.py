"""Real adapters: subprocess spawn of the hermes worker (§7.4), non-interactive
hermes boundary/replan invocation (best-effort — live use is §18.4), and the guarded
tui_gateway chat gateway (§11). Never imported into a test's execution path except
construction and the echo-script smoke."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Final

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import (
    BoundaryInputs,
    BoundaryJudgment,
    SpawnRequest,
    SpawnResult,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.days.contracts import NodeStatus, PlanNode, PlanTree
from planner.days.logic.tree import tree_from_dict

if TYPE_CHECKING:
    from planner.core.config import Config

BOUNDARY_SKILL: Final = "planning-boundary"     # §17 skill; §13 names no config key (RD-3)
_GATEWAY_MODULE: Final = "tui_gateway.ws"


def _parse_json_object(text: str) -> dict[str, Any]:
    """Slice the first ``{`` to the last ``}`` inclusive and parse. All failures
    raise; the caller event-logs them (RD-16)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in hermes output")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("hermes output is not a JSON object")
    result: dict[str, Any] = parsed
    return result


class RealSpawnAdapter:
    def __init__(self, config: Config) -> None:
        self._config = config
        # pid -> the live Popen of a child WE spawned. Retaining the handle lets the
        # server (the Popen parent) reap its own children via poll(); otherwise an
        # exited child lingers as a zombie and os.kill(pid, 0) reports it alive until
        # claim-TTL expiry (§7.1/§7.3 dead-worker detection).
        self._children: dict[int, subprocess.Popen[bytes]] = {}

    def spawn(self, request: SpawnRequest) -> SpawnResult:
        env = dict(os.environ)
        env["PLAN_SERVER_URL"] = request.server_url
        env["PLAN_TICKET_ID"] = request.ticket_id
        env["PLAN_RUN_ID"] = request.run_id
        env["PLAN_CLAIM"] = request.claim
        command = [
            request.hermes_bin,
            "-p",
            request.profile,
            "--skills",
            request.skill,
            "chat",
            "-q",
            f"work planning ticket {request.ticket_id}",
        ]
        try:
            with open(request.log_path, "wb") as log_file:  # parent fd closed after Popen
                process = subprocess.Popen(
                    command,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
        except OSError as exc:
            return SpawnResult(ok=False, error=str(exc))
        self._children[process.pid] = process
        return SpawnResult(ok=True, pid=process.pid)

    def is_pid_alive(self, pid: int) -> bool:
        """Per-pid dead-worker probe for the reclaim sweep (§7.1/§7.3). A pid we spawned
        is probed with Popen.poll(): None => still running (alive); a returncode => it has
        exited and poll() has just reaped it, so we drop the handle and report dead —
        detected within one tick, not at TTL. An untracked pid (e.g. a claim carried across
        a server restart, whose Popen this process never owned) falls back to the signal-0
        probe."""
        process = self._children.get(pid)
        if process is None:
            # Local import: real <- runtime would close the runtime -> registry -> real
            # import cycle at module load. _pid_alive is the single fallback impl.
            from planner.dispatch.runtime import _pid_alive

            return _pid_alive(pid)
        if process.poll() is None:
            return True
        del self._children[pid]
        return False

    def reap_finished_children(self) -> None:
        """Reap and drop every tracked child that has exited (Popen.poll() reaps it). The
        dispatcher calls this once per tick, so children of runs that closed by any path —
        which the reclaim sweep never probes by pid — are not left as zombies or retained
        handles. is_pid_alive only reaps the single pid it is asked about."""
        for done_pid in [p for p, proc in self._children.items() if proc.poll() is not None]:
            del self._children[done_pid]


class RealBoundaryAdapter:
    def __init__(self, config: Config) -> None:
        self._config = config

    def _invoke(self, prompt: str) -> str:
        """Run hermes non-interactively. The CALLER owns the timeout (no subprocess
        timeout here); a non-zero exit raises."""
        result = subprocess.run(
            [
                self._config.hermes_bin,
                "-p",
                self._config.hermes_profile,
                "--skills",
                BOUNDARY_SKILL,
                "chat",
                "-q",
                prompt,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            tail = result.stderr.strip()[-500:]
            raise RuntimeError(f"hermes exited {result.returncode}: {tail}")
        return result.stdout

    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
        prompt = (
            "You are the planning boundary judgment. Reply with exactly one JSON "
            'object {"focus": string, "brief_take": string, "watchout": string, '
            '"if_today_lands": string} and nothing else. Inputs: '
            + json.dumps(asdict(inputs))
        )
        payload = _parse_json_object(self._invoke(prompt))
        return BoundaryJudgment(
            focus=str(payload["focus"]),
            brief_take=str(payload["brief_take"]),
            watchout=str(payload["watchout"]),
            if_today_lands=str(payload["if_today_lands"]),
        )

    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
        prompt = (
            f"Replan the full day plan for {day_id}. Reply with exactly one JSON "
            'object {"plan_tree": {"root": {"focus", "status"}, "children": [...]}} '
            "and nothing else. Inputs: " + json.dumps(asdict(inputs))
        )
        payload = _parse_json_object(self._invoke(prompt))
        return tree_from_dict(payload["plan_tree"])

    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
        prompt = (
            f"Replan a single plan child for {day_id}. Reply with exactly one JSON "
            'object {"child": {"ticket_id": string|null, "note": string}} and nothing '
            "else. Child: "
            + json.dumps(
                {"ticket_id": child.ticket_id, "note": child.note, "position": child.position}
            )
            + " Inputs: "
            + json.dumps(asdict(inputs))
        )
        payload = _parse_json_object(self._invoke(prompt))
        node = payload["child"]
        ticket_id = node["ticket_id"]
        return PlanNode(
            ticket_id=str(ticket_id) if ticket_id is not None else None,
            note=str(node["note"]),
            status=NodeStatus.proposed,
            position=child.position,
        )


class RealGatewayAdapter:
    def __init__(self, config: Config) -> None:
        self._config = config

    def status(self) -> GatewayStatus:
        try:
            importlib.import_module(_GATEWAY_MODULE)
        except ImportError as exc:
            return GatewayStatus(available=False, detail=str(exc))
        return GatewayStatus(available=True)

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        try:
            module = importlib.import_module(_GATEWAY_MODULE)
        except ImportError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway unavailable", {"detail": str(exc)}
            ) from exc
        try:
            raw = module.send_message(
                session_key=session_key, entity_id=entity_id, text=text
            )
            return ChatSendResult(
                reply_text=str(raw["reply_text"]), session_key=str(raw["session_key"])
            )
        except Exception as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway send failed", {"detail": str(exc)}
            ) from exc
