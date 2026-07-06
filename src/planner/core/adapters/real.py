"""Real adapters: non-interactive hermes boundary invocation (best-effort — live use
is §18.4) and the guarded tui_gateway chat gateway (§11). Never imported into a test's
execution path except construction and the echo-script smoke.

(The old subprocess-spawn adapter was removed with the dispatcher; System B drives
minds via the W1 gateway primitive now.)"""

from __future__ import annotations

import importlib
import json
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Final

from planner.chat.contracts import ChatSendResult, GatewayStatus
from planner.core.adapters.base import BoundaryInputs, BoundaryJudgment
from planner.core.errors import ErrorCode, PlannerError

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
