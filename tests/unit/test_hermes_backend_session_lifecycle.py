"""Acceptance area 6 — lifecycle policy (plan §8 test_hermes_backend_session_lifecycle.py,
R-5/R-6/R3-E). Denylisted methods are rejected with a JSON-RPC error and never reach the
child; a same-session method forwards untouched; the denylist completeness is verified by
MECHANICALLY scanning the installed tui_gateway/server.py @method decorators."""

from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path

from planner.hermes_backend.employee_child_relay import (
    KNOWN_TUI_GATEWAY_LIFECYCLE_CAPABLE,
    KNOWN_TUI_GATEWAY_SESSION_METHODS,
    RELAY_LIFECYCLE_DENIED_CODE,
    SESSION_LIFECYCLE_DENYLIST,
    EmployeeChildRelay,
)
from planner.minds.config import hermes_src_root, resolve_hermes_python

E1 = "ticket_e1"


class _StubTransport:
    def __init__(self):
        self.alive = True
        self.sent = []

    def enqueue_frame(self, frame):
        self.sent.append(frame)


class _StubRecord:
    def __init__(self, generation):
        self.child_generation = generation


class _StubPool:
    def __init__(self, generation):
        self._gen = generation
        self.init_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def child_for_employee(self, employee):
        return _StubRecord(self._gen)


def _drain(conn):
    out = []
    while not conn.outbound.empty():
        out.append(conn.outbound.get_nowait())
    return out


def _req(inner):
    return json.dumps({"relay": "request", "employee_entity_id": E1, "frame": inner})


def test_downstream_lifecycle_methods_are_rejected_with_jsonrpc_error_and_never_reach_child() -> (
    None
):
    async def body():
        loop = asyncio.get_running_loop()
        t1 = _StubTransport()
        relay = EmployeeChildRelay(pool_provider=lambda: _StubPool(1), loop=loop)
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(1, E1, t1)  # type: ignore[arg-type]
        for i, method in enumerate(sorted(SESSION_LIFECYCLE_DENYLIST)):
            inner_id = 1000 + i
            forward = relay.handle_downstream_message(
                conn, _req({"jsonrpc": "2.0", "id": inner_id, "method": method})
            )
            assert forward is None  # rejected synchronously; no forward coroutine
            out = [json.loads(x) for x in _drain(conn)]
            assert len(out) == 1
            assert out[0]["id"] == inner_id
            assert out[0]["error"]["code"] == RELAY_LIFECYCLE_DENIED_CODE
            assert "relay" not in out[0]  # a JSON-RPC error, NOT a relay-control frame
        # No denylisted method ever reached the child.
        assert all(f.get("method") not in SESSION_LIFECYCLE_DENYLIST for f in t1.sent)

    asyncio.run(body())


def test_lifecycle_notification_without_id_is_dropped_silently() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        t1 = _StubTransport()
        relay = EmployeeChildRelay(pool_provider=lambda: _StubPool(1), loop=loop)
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(1, E1, t1)  # type: ignore[arg-type]
        forward = relay.handle_downstream_message(
            conn,
            _req({"jsonrpc": "2.0", "method": "session.create"}),  # no id
        )
        assert forward is None
        assert _drain(conn) == []  # dropped silently, no response
        assert t1.sent == []  # no child write

    asyncio.run(body())


def test_malformed_envelope_gets_relay_control_error() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        relay = EmployeeChildRelay(pool_provider=lambda: None, loop=loop)
        conn = relay.register_downstream()
        # Unknown relay verb.
        relay.handle_downstream_message(conn, json.dumps({"relay": "frobnicate"}))
        out = [json.loads(x) for x in _drain(conn)]
        assert out[0]["relay"] == "error"
        # Missing address on a request.
        relay.handle_downstream_message(conn, json.dumps({"relay": "request"}))
        out2 = [json.loads(x) for x in _drain(conn)]
        assert out2[0]["relay"] == "error"

    asyncio.run(body())


def test_non_binding_session_methods_forward_untouched() -> None:
    async def body():
        loop = asyncio.get_running_loop()
        t1 = _StubTransport()
        relay = EmployeeChildRelay(pool_provider=lambda: _StubPool(1), loop=loop)
        conn = relay.register_downstream()
        relay.subscribe(conn, [E1])
        relay.register_child(1, E1, t1)  # type: ignore[arg-type]
        methods = [
            "session.interrupt",
            "session.steer",
            "session.cwd.set",
            "prompt.submit",
            "clarify.respond",
        ]
        for i, method in enumerate(methods):
            f = relay.handle_downstream_message(
                conn, _req({"jsonrpc": "2.0", "id": 500 + i, "method": method})
            )
            assert f is not None  # forwarded (returns a _forward coroutine)
            await f
        forwarded = [f.get("method") for f in t1.sent]
        for method in methods:
            assert method in forwarded

    asyncio.run(body())


def _locate_tui_gateway_server_py() -> Path | None:
    override = os.environ.get("PLAN_TUI_GATEWAY_SERVER_PY")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    root = hermes_src_root(resolve_hermes_python())
    candidate = root / "tui_gateway" / "server.py"
    return candidate if candidate.exists() else None


def _scan_method_decorator_names(text: str) -> set[str]:
    """Robustly extract every `@method("...")` name via AST, so a future single-quoted
    `@method('session.foo')` (or other quote style) can NOT evade the drift trip-wire
    (defect #9b — a substring regex matched only double quotes)."""
    tree = ast.parse(text)
    names: set[str] = set()
    for node in ast.walk(tree):
        decorators = getattr(node, "decorator_list", None)
        if not decorators:
            continue
        for dec in decorators:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "method"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
                and isinstance(dec.args[0].value, str)
            ):
                names.add(dec.args[0].value)
    return names


def test_denylist_derived_by_scanning_tui_gateway_method_decorators() -> None:
    server_py = _locate_tui_gateway_server_py()
    assert server_py is not None, (
        "tui_gateway/server.py not found via PLAN_TUI_GATEWAY_SERVER_PY or the resolved "
        "Hermes checkout — the denylist drift trip-wire must run, never silently pass"
    )
    text = server_py.read_text()
    scanned = _scan_method_decorator_names(text)
    assert scanned, "no @method decorators found — AST scan is broken"
    scanned_session_methods = {m for m in scanned if m.startswith("session.")}
    # (a) drift trip-wire: a 21st (or removed) upstream session.* fails until refreshed.
    drift = scanned_session_methods ^ KNOWN_TUI_GATEWAY_SESSION_METHODS
    assert scanned_session_methods == KNOWN_TUI_GATEWAY_SESSION_METHODS, (
        f"tui_gateway session.* drift: {drift}"
    )
    # (b) the known binding-capable subset is fully denied.
    assert KNOWN_TUI_GATEWAY_LIFECYCLE_CAPABLE <= SESSION_LIFECYCLE_DENYLIST
    # (c) the six session.* binding members = KNOWN ∩ denylist, and number six.
    binding_members = KNOWN_TUI_GATEWAY_SESSION_METHODS & SESSION_LIFECYCLE_DENYLIST
    assert binding_members == {
        "session.create",
        "session.resume",
        "session.branch",
        "session.activate",
        "session.close",
        "session.delete",
    }
    assert len(binding_members) == 6
    # (d) session.cwd.set is a same-session mutation: scanned but NOT denied.
    assert "session.cwd.set" in scanned_session_methods
    assert "session.cwd.set" not in SESSION_LIFECYCLE_DENYLIST
    # The three generic hatches are denied AND present in the full scanned registry.
    for hatch in ("handoff.request", "cli.exec", "slash.exec"):
        assert hatch in SESSION_LIFECYCLE_DENYLIST
        assert hatch in scanned
