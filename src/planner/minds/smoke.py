"""Real-gateway smoke run — HUMAN-RUN ONLY. Mirrors spike 01 probes 1-3:
spawn -> ready -> session.create -> prompt.submit -> streamed events ->
message.complete; optional resume leg; deletes the session it created
(best effort). Talks to the REAL gateway and the REAL model: costs one
(or two, with --resume) model round trips and briefly creates a session
in the target HERMES_HOME.

Usage:
  .venv/bin/python -m planner.minds.smoke
      [--role SKILL] [--home DIR] [--prompt TEXT] [--resume]
      [--hermes-python PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from planner.minds.config import hermes_src_root, resolve_hermes_python
from planner.minds.gateway import GatewayChild, GatewayError, JsonDict


def _build_env(role: str | None, home: str | None, python: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))
    if home is not None:
        env["HERMES_HOME"] = str(Path(home).expanduser())
    if role is not None:
        env["HERMES_TUI_SKILLS"] = role
    return env


def _run_prompt(child: GatewayChild, live_sid: str, prompt: str) -> int:
    child.request("prompt.submit", {"session_id": live_sid, "text": prompt})
    while True:
        event = child.next_event(timeout=120.0)
        if event is None:
            print("[smoke] child died mid-run", file=sys.stderr)
            return 1
        etype = str(event.get("type") or "")
        raw = event.get("payload")
        payload: JsonDict = raw if isinstance(raw, dict) else {}
        if etype == "message.delta":
            sys.stdout.write(str(payload.get("text") or ""))
            sys.stdout.flush()
        else:
            print(f"[smoke] event: {etype}")
        if etype == "error":
            print(f"[smoke] error event: {payload.get('message')}", file=sys.stderr)
            return 1
        if etype == "message.complete":
            print()
            print(f"[smoke] complete: status={payload.get('status')} usage={payload.get('usage')}")
            print(f"[smoke] text: {payload.get('text')}")
            return 0


def _resume_leg(python: Path, env: dict[str, str], stored: str) -> int:
    child = GatewayChild(str(python), env)
    try:
        child.wait_ready()
        resumed = child.request("session.resume", {"session_id": stored})
        count = resumed.get("message_count")
        print(f"[smoke] resumed message_count={count}")
        if not isinstance(count, int) or count < 2:
            print("[smoke] resume did not carry prior turns", file=sys.stderr)
            return 1
        live_sid = str(resumed.get("session_id") or "")
        try:
            child.request("session.close", {"session_id": live_sid})
        except GatewayError as exc:
            print(f"[smoke] resume close failed: {exc}", file=sys.stderr)
    finally:
        child.shutdown()
    return 0


def _cleanup(python: Path, env: dict[str, str], stored: str) -> None:
    child = GatewayChild(str(python), env)
    try:
        child.wait_ready()
        try:
            deleted = child.request("session.delete", {"session_id": stored})
            print(f"[smoke] deleted: {deleted}")
        except GatewayError as exc:
            print(f"[smoke] delete failed (best effort): {exc}")
    finally:
        child.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-gateway smoke run (human-run only).")
    parser.add_argument("--role")
    parser.add_argument("--home")
    parser.add_argument("--prompt", default="Reply with exactly: ok")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--hermes-python", dest="hermes_python")
    args = parser.parse_args()

    role: str | None = args.role
    home: str | None = args.home
    prompt: str = args.prompt
    do_resume: bool = args.resume
    hermes_python_arg: str | None = args.hermes_python

    python = resolve_hermes_python(hermes_python_arg)
    env = _build_env(role, home, python)

    child = GatewayChild(str(python), env)
    stored = ""
    try:
        started = time.perf_counter()
        child.wait_ready()
        print(f"[smoke] ready in {time.perf_counter() - started:.3f}s")
        created = child.request("session.create", {"source": "minds-smoke", "cols": 100})
        live_sid = str(created.get("session_id") or "")
        stored = str(created.get("stored_session_id") or "")
        print(f"[smoke] live sid={live_sid} stored={stored}")
        rc = _run_prompt(child, live_sid, prompt)
        if rc != 0:
            return rc
        try:
            child.request("session.close", {"session_id": live_sid})
        except GatewayError as exc:
            print(f"[smoke] session.close failed: {exc}", file=sys.stderr)
    finally:
        child.shutdown()

    if do_resume and stored:
        rc = _resume_leg(python, env, stored)
        if rc != 0:
            return rc

    if stored:
        _cleanup(python, env, stored)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
