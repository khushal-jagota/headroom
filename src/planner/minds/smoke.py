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

  .venv/bin/python -m planner.minds.smoke --concurrency
      [--role SKILL] [--home DIR] [--hermes-python PATH]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from planner.minds.config import hermes_src_root, resolve_hermes_python
from planner.minds.gateway import (
    ChildSessionEventIngress,
    GatewayChild,
    GatewayError,
    GatewayRpcError,
    JsonDict,
)

BUSY_CODE = 4009
TURN_TIMEOUT = 180.0


@dataclass(frozen=True)
class _TurnCheck:
    name: str
    ok: bool
    message: str
    text: str = ""


def _build_env(role: str | None, home: str | None, python: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))
    if home is not None:
        env["HERMES_HOME"] = str(Path(home).expanduser())
    if role is not None:
        env["HERMES_TUI_SKILLS"] = role
    return env


def _run_prompt(
    child: GatewayChild,
    ingress: ChildSessionEventIngress,
    live_sid: str,
    prompt: str,
) -> int:
    child.request("prompt.submit", {"session_id": live_sid, "text": prompt})
    while True:
        event = ingress.next_event(timeout=120.0)
        if event is None:
            print("[smoke] child died mid-run", file=sys.stderr)
            return 1
        wrong_session = _assert_own_event(event, live_sid, "prompt")
        if wrong_session is not None:
            print(f"[smoke] {wrong_session.message}", file=sys.stderr)
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
            print(
                f"[smoke] complete: status={payload.get('status')} "
                f"usage={payload.get('usage')}"
            )
            print(f"[smoke] text: {payload.get('text')}")
            return 0


def _create_session(child: GatewayChild, source: str) -> tuple[str, str]:
    created = child.request("session.create", {"source": source, "cols": 100})
    live_sid = str(created.get("session_id") or "")
    stored = str(created.get("stored_session_id") or "")
    if not live_sid or not stored:
        raise GatewayError(f"session.create returned invalid ids: {created}")
    print(f"[smoke:concurrency] created live sid={live_sid} stored={stored}")
    return live_sid, stored


def _close_live_session(child: GatewayChild, live_sid: str) -> None:
    try:
        child.request("session.close", {"session_id": live_sid})
    except GatewayError as exc:
        print(f"[smoke:concurrency] session.close failed for {live_sid}: {exc}", file=sys.stderr)


def _payload(event: JsonDict) -> JsonDict:
    raw = event.get("payload")
    return raw if isinstance(raw, dict) else {}


def _assert_own_event(event: JsonDict, live_sid: str, name: str) -> _TurnCheck | None:
    raw_session_id = event.get("session_id")
    if str(raw_session_id or "") != live_sid:
        return _TurnCheck(
            name,
            False,
            f"received event for session {raw_session_id!r}, expected {live_sid!r}",
        )
    return None


def _submit_concurrent_turn(
    child: GatewayChild,
    *,
    name: str,
    live_sid: str,
    prompt: str,
    barrier: threading.Barrier,
) -> _TurnCheck:
    try:
        barrier.wait(timeout=10.0)
        child.request("prompt.submit", {"session_id": live_sid, "text": prompt})
        return _TurnCheck(name, True, "prompt accepted")
    except (GatewayError, threading.BrokenBarrierError) as exc:
        return _TurnCheck(name, False, str(exc))


def _check_distinct_sessions_demux(
    child: GatewayChild,
    ingress: ChildSessionEventIngress,
    sid_a: str,
    sid_b: str,
) -> bool:
    print("[smoke:concurrency] CHECK distinct sessions on one child: START")
    barrier = threading.Barrier(3)
    prompt_a = "Reply with exactly ALPHA and no other text."
    prompt_b = "Reply with exactly BRAVO and no other text."
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(
            _submit_concurrent_turn,
            child,
            name="session A",
            live_sid=sid_a,
            prompt=prompt_a,
            barrier=barrier,
        )
        future_b = executor.submit(
            _submit_concurrent_turn,
            child,
            name="session B",
            live_sid=sid_b,
            prompt=prompt_b,
            barrier=barrier,
        )
        try:
            barrier.wait(timeout=10.0)
        except threading.BrokenBarrierError:
            print("[smoke:concurrency] FAIL distinct sessions: workers did not start")
            return False
        try:
            submissions = [
                future_a.result(timeout=TURN_TIMEOUT + 30.0),
                future_b.result(timeout=TURN_TIMEOUT + 30.0),
            ]
        except concurrent.futures.TimeoutError:
            print("[smoke:concurrency] FAIL distinct sessions: timed out waiting for workers")
            return False
    for submission in submissions:
        if not submission.ok:
            print(f"[smoke:concurrency] FAIL {submission.name}: {submission.message}")
            return False

    expected_by_session = {
        sid_a: ("session A", "ALPHA", "BRAVO"),
        sid_b: ("session B", "BRAVO", "ALPHA"),
    }
    checks: list[_TurnCheck] = []
    while expected_by_session:
        event = ingress.next_event(timeout=TURN_TIMEOUT)
        if event is None:
            print("[smoke:concurrency] FAIL distinct sessions: gateway child died")
            return False
        session_id = str(event.get("session_id") or "")
        expected = expected_by_session.get(session_id)
        if expected is None:
            print(
                "[smoke:concurrency] FAIL distinct sessions: "
                f"received event for unexpected session {session_id!r}"
            )
            return False
        name, expected_text, forbidden = expected
        etype = str(event.get("type") or "")
        payload = _payload(event)
        if etype == "error":
            checks.append(
                _TurnCheck(name, False, f"gateway error event: {payload.get('message')}")
            )
            expected_by_session.pop(session_id)
        elif etype == "message.complete":
            text = str(payload.get("text") or "")
            stripped = text.strip()
            ok = stripped == expected_text and forbidden not in text
            checks.append(
                _TurnCheck(
                    name,
                    ok,
                    f"received exactly {expected_text!r}"
                    if ok
                    else f"expected exactly {expected_text!r}, got {stripped!r}",
                    text,
                )
            )
            expected_by_session.pop(session_id)
    ok = True
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        print(f"[smoke:concurrency] {status} {check.name}: {check.message}")
        ok = ok and check.ok
    status = "PASS" if ok else "FAIL"
    print(f"[smoke:concurrency] {status} distinct sessions on one child")
    return ok


def _check_same_session_busy(
    child: GatewayChild,
    ingress: ChildSessionEventIngress,
    live_sid: str,
) -> bool:
    print("[smoke:concurrency] CHECK same-session 4009 busy guard: START")
    prompt = (
        "Concurrency smoke: produce exactly 80 numbered lines. "
        "Each line must be of the form BUSY-SMOKE-N where N increases from 1 to 80. "
        "Do not summarize and do not stop early."
    )
    try:
        child.request("prompt.submit", {"session_id": live_sid, "text": prompt})
    except GatewayError as exc:
        print(f"[smoke:concurrency] FAIL same-session first submit: {exc}")
        return False

    try:
        child.request(
            "prompt.submit",
            {"session_id": live_sid, "text": "Reply with SHOULD_NOT_RUN."},
            timeout=10.0,
        )
    except GatewayRpcError as exc:
        if exc.code != BUSY_CODE:
            print(
                "[smoke:concurrency] FAIL same-session busy guard: "
                f"expected 4009, got {exc.code}",
            )
            return False
        print("[smoke:concurrency] PASS same-session busy guard: got 4009 session busy")
    except GatewayError as exc:
        print(f"[smoke:concurrency] FAIL same-session busy guard: {exc}")
        return False
    else:
        print("[smoke:concurrency] FAIL same-session busy guard: second submit was accepted")
        return False

    while True:
        event = ingress.next_event(timeout=TURN_TIMEOUT)
        if event is None:
            print("[smoke:concurrency] FAIL same-session drain: gateway child died mid-run")
            return False
        wrong_session = _assert_own_event(event, live_sid, "same-session drain")
        if wrong_session is not None:
            print(f"[smoke:concurrency] FAIL same-session drain: {wrong_session.message}")
            return False
        etype = str(event.get("type") or "")
        payload = _payload(event)
        if etype == "error":
            print(
                "[smoke:concurrency] FAIL same-session drain: "
                f"gateway error event: {payload.get('message')}"
            )
            return False
        if etype == "message.complete":
            print("[smoke:concurrency] PASS same-session first turn drained cleanly")
            return True


def _resume_leg(python: Path, env: dict[str, str], stored: str) -> int:
    child = GatewayChild(str(python), env)
    ingress = child.claim_session_event_ingress()
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
        ingress.close()
        child.shutdown()
    return 0


def _run_concurrency_smoke(python: Path, env: dict[str, str]) -> int:
    child = GatewayChild(str(python), env)
    ingress = child.claim_session_event_ingress()
    live_sessions: list[str] = []
    stored_sessions: list[str] = []
    demux_ok = False
    busy_ok = False
    try:
        started = time.perf_counter()
        child.wait_ready()
        print(f"[smoke:concurrency] ready in {time.perf_counter() - started:.3f}s")

        try:
            sid_a, stored_a = _create_session(child, "minds-smoke-concurrency-a")
            sid_b, stored_b = _create_session(child, "minds-smoke-concurrency-b")
            live_sessions.extend([sid_a, sid_b])
            stored_sessions.extend([stored_a, stored_b])
            demux_ok = _check_distinct_sessions_demux(child, ingress, sid_a, sid_b)
        except GatewayError as exc:
            print(f"[smoke:concurrency] FAIL distinct sessions setup/run: {exc}")

        try:
            sid_busy, stored_busy = _create_session(child, "minds-smoke-concurrency-busy")
            live_sessions.append(sid_busy)
            stored_sessions.append(stored_busy)
            busy_ok = _check_same_session_busy(child, ingress, sid_busy)
        except GatewayError as exc:
            print(f"[smoke:concurrency] FAIL same-session busy setup/run: {exc}")

        for live_sid in live_sessions:
            _close_live_session(child, live_sid)
    finally:
        ingress.close()
        child.shutdown()

    for stored in stored_sessions:
        _cleanup(python, env, stored)

    return 0 if demux_ok and busy_ok else 1


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
    parser.add_argument("--concurrency", action="store_true")
    parser.add_argument("--hermes-python", dest="hermes_python")
    args = parser.parse_args()

    role: str | None = args.role
    home: str | None = args.home
    prompt: str = args.prompt
    do_resume: bool = args.resume
    do_concurrency: bool = args.concurrency
    hermes_python_arg: str | None = args.hermes_python

    python = resolve_hermes_python(hermes_python_arg)
    env = _build_env(role, home, python)

    if do_concurrency:
        return _run_concurrency_smoke(python, env)

    child = GatewayChild(str(python), env)
    ingress = child.claim_session_event_ingress()
    stored = ""
    try:
        started = time.perf_counter()
        child.wait_ready()
        print(f"[smoke] ready in {time.perf_counter() - started:.3f}s")
        created = child.request("session.create", {"source": "minds-smoke", "cols": 100})
        live_sid = str(created.get("session_id") or "")
        stored = str(created.get("stored_session_id") or "")
        print(f"[smoke] live sid={live_sid} stored={stored}")
        rc = _run_prompt(child, ingress, live_sid, prompt)
        if rc != 0:
            return rc
        try:
            child.request("session.close", {"session_id": live_sid})
        except GatewayError as exc:
            print(f"[smoke] session.close failed: {exc}", file=sys.stderr)
    finally:
        ingress.close()
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
