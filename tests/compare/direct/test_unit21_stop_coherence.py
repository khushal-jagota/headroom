"""One hosted owned-process proof: HTTP/iterator lifetime, repeated TERM, failed stop.

Reuse the existing private server fixture and launcher. No browser, provider, live
service, or additional server framework is involved. This stays hosted-only.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import socket
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.server_fixtures import _start_server, _stop_server

ROOT = Path(__file__).resolve().parents[3]


def _state(root: Path, name: str) -> Any:
    path = root / f"{name}.json"
    until = time.monotonic() + 15
    while not path.exists():
        assert time.monotonic() < until, f"Missing owned-host state: {name}"
        time.sleep(0.01)
    return json.loads(path.read_text())


def _owner(path: Path, held: bool) -> None:
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            assert held
        else:
            assert not held


@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true",
    reason="Owned HTTP/signal lifecycle proof runs on hosted CI only",
)
def test_unit21_stop_retains_disconnected_work_and_failed_cleanup(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    began = time.perf_counter()
    selected_node = shutil.which("node")
    assert selected_node, "The selected Node executable is required"
    runtime = tmp_path / "runtime"
    entry = tmp_path / "stop-proof.mjs"
    companion = ROOT / "server/lifecycle/testing/stop.ts"
    entry.write_text(
        f"import{{stop_host_proof}}from{json.dumps(companion.as_uri())};"
        f"await stop_host_proof({json.dumps({'root': str(runtime)})});\n"
    )
    server = _start_server(
        runtime,
        server_module="tests.compare.ts_launcher",
        trusted_ingress_env={
            "PANELS_COMPARE_TS_ENTRY": str(entry),
            "PATH": str(Path(selected_node).parent) + os.pathsep + os.defpath,
            "PLAN_SERVER_LIFECYCLE_LEASE_PATH": str(runtime / "run/lifecycle.lock"),
        },
    )
    clients: list[socket.socket] = []
    owner = Path(str(server.db_path) + ".conversation-owner.lock")
    try:
        for route in ("held", "tail"):
            client = socket.create_connection(("127.0.0.1", server.port), timeout=10)
            clients.append(client)
            client.sendall(f"GET /proof/{route} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        _state(runtime, "handler-entered")
        _state(runtime, "iterator-entered")
        for client in clients:
            client.close()
        _state(runtime, "handler-disconnected")
        _state(runtime, "iterator-disconnected")
        assert httpx.post(server.base + "/proof/stop", timeout=10).json() == {"accepted": True}
        retained = _state(runtime, "retained")
        assert retained["state"]["kind"] == "retained"
        assert "HTTP work" in retained["state"]["pending"]
        assert not (runtime / "unexpected-close.json").exists()
        _owner(owner, True)
        server.proc.send_signal(signal.SIGTERM)
        _state(runtime, "first-term")
        server.proc.send_signal(signal.SIGTERM)
        (runtime / "first-ping").touch()
        assert _state(runtime, "first-pong") is True
        assert server.proc.poll() is None
        _owner(owner, True)
        assert server.log_path.read_text().count("ShutdownDeadlineExceeded:") == 1
        (runtime / "release-handler").touch()
        (runtime / "release-iterator").touch()
        assert _state(runtime, "handler-settled") == {"alive": 1}
        assert _state(runtime, "iterator-settled") == {"alive": 1}
        assert _state(runtime, "iterator-returned") is True
        assert _state(runtime, "clean-finish")["kind"] == "closed"
        failed = _state(runtime, "failure-observed")
        assert failed["actual_settled"] is False
        assert failed["state"]["kind"] == "retained"
        assert "backend services" in failed["state"]["pending"]
        assert "pending: backend services" in failed["error"]
        assert "video transfers" in failed["error"]
        assert [item["name"] for item in failed["state"]["failures"]] == ["video transfers"]
        assert _state(runtime, "core-fenced") is True
        assert _state(runtime, "backend-fenced") is True
        assert _state(runtime, "reentry") == {"same_flight": True}
        _owner(owner, True)
        (runtime / "release-failure").touch()
        settled = _state(runtime, "failure-finished")
        assert settled["actual_settled"] is True
        assert settled["database"] == {"alive": 1}
        assert settled["refused"] and settled["state"]["kind"] == "retained"
        (runtime / "failed-ping").touch()
        assert _state(runtime, "failed-pong") is True
        assert server.proc.poll() is None
        _owner(owner, True)
        # A failed cleanup has no product bypass. Terminate only this owned child.
        server.proc.kill()
        server.proc.wait(timeout=10)
        _owner(owner, False)
        report = request.config.getoption("compare_report_dir", default=None)
        if report:
            destination = Path(report)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "unit-21-stop-coherence.json").write_text(
                json.dumps(
                    {
                        "pid": server.proc.pid,
                        "seconds": time.perf_counter() - began,
                        "timeout": retained,
                        "failure": settled,
                        "boundary": (
                            "One private fixture process, two sequential app lifetimes; "
                            "combined HTTP/iterator disconnect ownership, repeated TERM, "
                            "retention and failed-finalization refusal; individual lifetime "
                            "gates use the separate cheap unit proof; no browser."
                        ),
                    },
                    indent=2,
                )
                + "\n"
            )
    finally:
        for client in clients:
            client.close()
        _stop_server(server)
