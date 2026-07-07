"""Real adapters.

The boundary adapter still invokes Hermes non-interactively. The chat gateway's
real implementation is the app-state SharedGateway installed by server startup;
this module's RealGatewayAdapter is only a registry placeholder and never owns a
GatewayChild.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Final

from planner.chat.contracts import (
    ChatSendResult,
    CommandCatalog,
    CommandRunResult,
    GatewayStatus,
)
from planner.core.adapters.base import BoundaryInputs, BoundaryJudgment
from planner.core.errors import ErrorCode, PlannerError

if TYPE_CHECKING:
    from planner.core.config import Config

BOUNDARY_SKILL: Final = "planning-boundary"


def _parse_json_object(text: str) -> dict[str, Any]:
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
    """Registry placeholder; create_app replaces it with SharedGateway in production."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def _offline(self) -> PlannerError:
        return PlannerError(
            ErrorCode.gateway_offline,
            "shared gateway is not attached",
            {"detail": "create_app installs SharedGateway in production startup"},
        )

    def status(self) -> GatewayStatus:
        from planner.minds.config import resolve_hermes_python

        python = resolve_hermes_python()
        if not python.exists():
            return GatewayStatus(available=False, detail=f"hermes interpreter not found: {python}")
        return GatewayStatus(available=False, detail="shared gateway is not attached")

    def send(self, session_key: str | None, entity_id: str, text: str) -> ChatSendResult:
        raise self._offline()

    def catalog(self) -> CommandCatalog:
        raise self._offline()

    def run_command(
        self, session_key: str | None, entity_id: str, command: str
    ) -> CommandRunResult:
        raise self._offline()
