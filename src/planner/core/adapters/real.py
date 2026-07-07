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

from planner.chat.contracts import (
    ChatSendResult,
    CommandCatalog,
    CommandCategory,
    CommandRunResult,
    GatewayStatus,
)
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

    def catalog(self) -> CommandCatalog:
        """Stateless read of the gateway's command/skill registry. A throwaway child
        (same env as send, no session) → commands.catalog → shutdown. Nothing is
        created, so nothing is left to clean up (spike §0)."""
        from planner.minds.config import resolve_hermes_python
        from planner.minds.gateway import GatewayChild, GatewayError

        python = resolve_hermes_python()
        child = GatewayChild(str(python), self._env(python))
        try:
            child.wait_ready()
            raw = child.request("commands.catalog")
            return self._build_catalog(raw)
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway catalog failed", {"detail": str(exc)}
            ) from exc
        finally:
            child.shutdown()

    def run_command(
        self, session_key: str | None, entity_id: str, command: str
    ) -> CommandRunResult:
        """Run a /command on the entity's own mind (spike §3). Resume/create the
        session (identical to send), then slash.exec; a skill/pending-input command
        answers RPC 4018 → command.dispatch, whose typed payload drives the next step
        (skill/send → prompt.submit the message and drain to the model reply; exec/
        plugin → display output)."""
        from planner.minds.config import resolve_hermes_python
        from planner.minds.gateway import GatewayChild, GatewayError, GatewayRpcError

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
            name, arg = self._split_command(command)
            try:
                result = child.request("slash.exec", {"session_id": live_sid, "command": command})
            except GatewayRpcError as exc:
                if exc.code != 4018:  # not a skill/pending-input; a genuine command error
                    raise
                payload = child.request(
                    "command.dispatch", {"session_id": live_sid, "name": name, "arg": arg}
                )
                reply, kind = self._interpret(child, live_sid, payload, arg)
                return CommandRunResult(reply_text=reply, session_key=stored, kind=kind)
            # slash.exec succeeded. Pending-input commands forward to command.dispatch and
            # return its TYPED payload directly (server.py:10080-10091); a plain command
            # returns {output}. Interpret a typed payload; else it is display output.
            if result.get("type"):
                reply, kind = self._interpret(child, live_sid, result, arg)
                return CommandRunResult(reply_text=reply, session_key=stored, kind=kind)
            return CommandRunResult(
                reply_text=str(result.get("output") or ""), session_key=stored, kind="system"
            )
        except GatewayError as exc:
            raise PlannerError(
                ErrorCode.gateway_offline, "chat gateway command failed", {"detail": str(exc)}
            ) from exc
        finally:
            child.shutdown()

    def _interpret(
        self,
        child: Any,
        live_sid: str,
        payload: dict[str, Any],
        arg: str,
        *,
        alias_ok: bool = True,
    ) -> tuple[str, str]:
        """Turn a dispatch payload into (reply_text, kind): skill/send → prompt.submit the
        message and drain to the model reply; exec/plugin → display output; alias → resolve
        the target once, preserving the caller's args, and re-interpret (spike §3, §7.3)."""
        ptype = str(payload.get("type") or "")
        if ptype in ("skill", "send"):
            message = str(payload.get("message") or "")
            return self._submit_and_drain(child, live_sid, message), "assistant"
        if ptype in ("exec", "plugin"):
            return str(payload.get("output") or ""), "system"
        if ptype == "alias" and alias_ok:
            target_name, target_arg = self._split_command(str(payload.get("target") or ""))
            combined = f"{target_arg} {arg}".strip() if target_arg and arg else (arg or target_arg)
            resolved = child.request(
                "command.dispatch", {"session_id": live_sid, "name": target_name, "arg": combined}
            )
            return self._interpret(child, live_sid, resolved, combined, alias_ok=False)
        # unknown type, or a second alias hop → best-effort display line
        return str(payload.get("output") or payload.get("message") or ""), "system"

    def _submit_and_drain(self, child: Any, live_sid: str, text: str) -> str:
        """Submit a prompt and drain to the single message.complete (the send drain
        shape). Raises on an error event or a dead child."""
        child.request("prompt.submit", {"session_id": live_sid, "text": text})
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
                return str(payload.get("text") or "")

    @staticmethod
    def _split_command(command: str) -> tuple[str, str]:
        """"/name args" → ("name", "args"). The leading slash is stripped; dispatch
        also lstrips it, so either form is accepted."""
        parts = command.strip().split(maxsplit=1)
        name = parts[0].lstrip("/") if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        return name, arg

    @staticmethod
    def _build_catalog(raw: dict[str, Any]) -> CommandCatalog:
        """Shape commands.catalog into our contract: the Skills group is exactly the
        last skill_count pairs (absent from categories — spike §0); raw pairs dropped."""
        def _pairs(seq: Any) -> tuple[tuple[str, str], ...]:
            out: list[tuple[str, str]] = []
            for item in seq or []:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    out.append((str(item[0]), str(item[1])))
            return tuple(out)

        pairs = raw.get("pairs") or []
        skill_count = int(raw.get("skill_count") or 0)
        skills = _pairs(pairs[-skill_count:]) if skill_count > 0 else ()
        categories = tuple(
            CommandCategory(name=str(cat.get("name") or ""), pairs=_pairs(cat.get("pairs")))
            for cat in (raw.get("categories") or [])
            if isinstance(cat, dict)
        )
        canon_raw = raw.get("canon")
        canon = (
            {str(k): str(v) for k, v in canon_raw.items()} if isinstance(canon_raw, dict) else {}
        )
        sub_raw = raw.get("sub")
        sub = (
            {str(k): [str(x) for x in (v or [])] for k, v in sub_raw.items()}
            if isinstance(sub_raw, dict)
            else {}
        )
        return CommandCatalog(categories=categories, skills=skills, canon=canon, sub=sub)
