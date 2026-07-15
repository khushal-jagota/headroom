"""Public CLI and wire-protocol seams for controlled Panels restarts."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from planner.cli.main import main


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _socket_path(label: str) -> Path:
    return Path("/tmp") / f"panels-{label}-{uuid.uuid4().hex[:10]}.sock"


def _run_control_listener(
    socket_path: Path,
    reply: bytes,
) -> tuple[list[dict[str, Any]], threading.Thread]:
    received: list[dict[str, Any]] = []
    ready = threading.Event()

    def listen() -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(socket_path))
                listener.listen(1)
                listener.settimeout(1.0)
                ready.set()
                connection, _ = listener.accept()
                with connection:
                    request = b""
                    while not request.endswith(b"\n"):
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        request += chunk
                    received.append(json.loads(request))
                    connection.sendall(reply)
        finally:
            socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)
    return received, thread


def test_restart_without_owner_only_reports_connection_error() -> None:
    socket_path = _socket_path("absent")
    port = _free_port()
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = CliRunner().invoke(
            main,
            ["restart"],
            env={
                "PLAN_SERVER_CONTROL_SOCKET": str(socket_path),
                "PLAN_PORT": str(port),
            },
        )

        assert result.exit_code != 0
        assert "Could not connect to the Panels supervisor" in result.stderr
        assert "Panels restart accepted." not in result.stdout
        assert not socket_path.exists()
        assert sentinel.poll() is None
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5.0)


def test_restart_rejects_malformed_and_incompatible_control_replies() -> None:
    replies = (
        b"not json\n",
        json.dumps({"version": 999, "status": "accepted"}).encode() + b"\n",
        json.dumps({"version": True, "status": "accepted"}).encode() + b"\n",
    )
    for index, reply in enumerate(replies):
        socket_path = _socket_path(f"malformed-{index}")
        received, thread = _run_control_listener(socket_path, reply)

        result = CliRunner().invoke(
            main,
            ["restart"],
            env={"PLAN_SERVER_CONTROL_SOCKET": str(socket_path)},
        )

        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert received == [{"version": 1, "operation": "restart"}]
        assert result.exit_code != 0
        assert "Panels supervisor returned an incompatible response" in result.stderr
        assert "Panels restart accepted." not in result.stdout


def test_restart_sends_versioned_request_and_prints_acceptance() -> None:
    socket_path = _socket_path("accepted")
    reply = json.dumps({"version": 1, "status": "accepted"}).encode() + b"\n"
    received, thread = _run_control_listener(socket_path, reply)

    result = CliRunner().invoke(
        main,
        ["restart"],
        env={"PLAN_SERVER_CONTROL_SOCKET": str(socket_path)},
    )

    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert received == [{"version": 1, "operation": "restart"}]
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == "Panels restart accepted.\n"
