"""run_step: a single-child Hermes smoke/helper primitive.

Production ticket execution uses SharedGateway. This helper remains useful for
protocol smoke tests: spawn a gateway child with role env, create/resume the
durable session, submit one prompt, drain events to the single message.complete
(or error event / child death), and reap the child.

No overall wall-clock timeout by design (notes.md: REMOVE timeout-as-failure);
ready/request timeouts guard only the protocol handshake."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from planner.minds.config import hermes_src_root
from planner.minds.contracts import OnEvent, RunResult
from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    REQUEST_TIMEOUT_DEFAULT,
    GatewayChild,
    GatewayError,
    GatewayRpcError,
    JsonDict,
    SpawnFn,
    spawn_popen,
)

SESSION_SOURCE: Final = "planner"
SESSION_COLS: Final = 100


def _notify(on_event: OnEvent | None, event: JsonDict) -> None:
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logging.getLogger(__name__).exception("on_event observer failed")


def run_step(
    session_key: str | None,
    role: str,
    prompt_text: str,
    on_event: OnEvent | None,
    *,
    home: str | Path,
    hermes_python: str | Path,
    context_env: Mapping[str, str] | None = None,
    spawn: SpawnFn = spawn_popen,
    ready_timeout: float = READY_TIMEOUT_DEFAULT,
    request_timeout: float = REQUEST_TIMEOUT_DEFAULT,
    base_env: Mapping[str, str] | None = None,
) -> RunResult:
    python = Path(hermes_python).expanduser()
    env: dict[str, str] = dict(base_env if base_env is not None else os.environ)
    env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))  # entry.py:4-12 guard
    env["HERMES_HOME"] = str(Path(home).expanduser())  # spike isolation probe
    env["HERMES_TUI_SKILLS"] = role  # spike §2 Q2 — per-process role skill
    if context_env:
        env.update(context_env)  # kanban HERMES_KANBAN_* pattern

    try:
        child = GatewayChild(str(python), env, spawn=spawn)
    except GatewayError as exc:
        return RunResult("errored", "", None, session_key, f"spawn failed: {exc}")

    resolved_key = session_key
    try:
        child.wait_ready(ready_timeout)
        if session_key is None:
            created = child.request(
                "session.create",
                {"source": SESSION_SOURCE, "cols": SESSION_COLS},
                timeout=request_timeout,
            )
            live_sid = str(created.get("session_id") or "")
            stored = created.get("stored_session_id")  # server.py:4417
            resolved_key = str(stored) if stored else None
        else:
            resumed = child.request(
                "session.resume", {"session_id": session_key}, timeout=request_timeout
            )
            live_sid = str(resumed.get("session_id") or "")  # NEW live handle
            tip = resumed.get("resumed")  # durable key, maybe rotated tip
            resolved_key = str(tip) if tip else session_key  # server.py:4612-4619, 4633
        with child.open_session_events(live_sid) as events:
            child.request(
                "prompt.submit",
                {"session_id": live_sid, "text": prompt_text},
                timeout=request_timeout,
            )  # {"status":"streaming"} — value unused
            while True:  # D5: no deadline
                event = events.next_event()
                if event is None:
                    return RunResult(
                        "errored",
                        "",
                        None,
                        resolved_key,
                        f"gateway child died mid-run; stderr: {child.stderr_tail()!r}",
                    )
                _notify(on_event, event)  # D8: forward everything, guarded
                etype = str(event.get("type") or "")
                if etype == "error":
                    raw = event.get("payload")
                    payload = raw if isinstance(raw, dict) else {}
                    return RunResult(
                        "errored",
                        "",
                        None,
                        resolved_key,
                        str(payload.get("message") or "gateway error event"),
                    )
                if etype == "message.complete":
                    raw = event.get("payload")
                    payload = raw if isinstance(raw, dict) else {}
                    text = str(payload.get("text") or "")
                    usage_raw = payload.get("usage")
                    usage = usage_raw if isinstance(usage_raw, dict) else None
                    gw_status = str(payload.get("status") or "complete")
                    if gw_status == "complete":
                        return RunResult("complete", text, usage, resolved_key, None)
                    if gw_status == "interrupted":
                        return RunResult("interrupted", text, usage, resolved_key, None)
                    return RunResult(
                        "errored",
                        text,
                        usage,
                        resolved_key,
                        text or "run ended with status=error",
                    )
                # any other event type: forwarded above, otherwise ignored — keep draining
    except GatewayRpcError as exc:
        return RunResult("errored", "", None, resolved_key, str(exc))
    except GatewayError as exc:
        return RunResult("errored", "", None, resolved_key, str(exc))
    finally:
        child.shutdown()  # child ALWAYS reaped
