"""Real adapters: non-interactive hermes boundary invocation (best-effort — live use
is §18.4) and the guarded tui_gateway chat gateway (§11). Never imported into a test's
execution path except construction and the echo-script smoke.

(The old subprocess-spawn adapter was removed with the dispatcher; System B drives
minds via the W1 gateway primitive now.)"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import BoundaryInputs, BoundaryJudgment
from planner.core.errors import ErrorCode, PlannerError

if TYPE_CHECKING:
    from planner.core.config import Config

BOUNDARY_SKILL: Final = "planning-boundary"     # §17 skill; §13 names no config key (RD-3)


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


class RealGatewayAdapter:
    """Chat gateway over the Option 3 stdio gateway (the W1 minds primitive): spawn the
    Hermes tui_gateway as a subprocess, resume/create the entity's mind, submit the chat text
    as a prompt, drain to the single message.complete, return the reply + durable key. Uses
    the owner's default HERMES_HOME (which has creds) — deliberately does NOT set HERMES_HOME;
    a dedicated planner home is the out-of-band follow-up."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def _env(self, python: Path) -> dict[str, str]:
        from planner.minds.config import hermes_src_root

        env = dict(os.environ)
        env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))
        return env  # no HERMES_HOME → the owner's default hermes home (with creds)

    def status(self) -> GatewayStatus:
        from planner.minds.config import resolve_hermes_python

        python = resolve_hermes_python()
        if python.exists():
            return GatewayStatus(available=True)
        return GatewayStatus(available=False, detail=f"hermes interpreter not found: {python}")

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        from planner.minds.config import resolve_hermes_python
        from planner.minds.gateway import GatewayChild, GatewayError

        python = resolve_hermes_python()
        child = GatewayChild(str(python), self._env(python))
        try:
            child.wait_ready()
            if session_key:
                resumed = child.request("session.resume", {"session_id": session_key})
                live_sid = str(resumed.get("session_id") or "")
                stored = str(resumed.get("resumed") or session_key)
            else:
                created = child.request("session.create", {"source": "planner-chat", "cols": 100})
                live_sid = str(created.get("session_id") or "")
                stored = str(created.get("stored_session_id") or "")
            child.request("prompt.submit", {"session_id": live_sid, "text": text})
            reply = ""
            while True:
                event = child.next_event(timeout=180.0)
                if event is None:
                    raise PlannerError(
                        ErrorCode.gateway_offline, "chat gateway: child died mid-run", {}
                    )
                etype = str(event.get("type") or "")
                raw = event.get("payload")
                payload = raw if isinstance(raw, dict) else {}
                if etype == "error":
                    raise PlannerError(
                        ErrorCode.gateway_offline,
                        "chat gateway error",
                        {"detail": str(payload.get("message") or "")},
                    )
                if etype == "message.complete":
                    reply = str(payload.get("text") or "")
                    break
            return ChatSendResult(reply_text=reply, session_key=stored)
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway send failed", {"detail": str(exc)}
            ) from exc
        finally:
            child.shutdown()
