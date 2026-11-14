"""Fresh Python CLI parity at the real Unix boundary, with one private scripted listener.

Risk/gap: existing CLI checks do not cross the executor socket. This proof compares
framing, authority, output/exit status, and uncertainty after lost/malformed replies.
No application server, database, provider, or TCP destination is involved.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from tests.compare.direct.mac_executor_fixture import report_direct, run_node
from tests.support.executor_presence import mac_identity
from tests.support.synthetic_homes import create_synthetic_homes

from planner.cli.main import main
from planner.executor import ipc_client
from planner.executor.protocol import IpcOperation, IpcRequest, as_object, type_adapter


def scenarios() -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = [
        dict(mode="ok", argv=["worker", "recap", "--json"], stdin="A recap"),
        dict(mode="ok", argv=["worker", "recap"], stdin="A recap"),
        dict(mode="text", argv=["manager", "artifact", "read", "manager", "file.txt"]),
        dict(mode="ok", argv=["executor", "retry", "original", "--json"]),
        dict(mode="health", argv=["executor", "health"]),
        dict(mode="ok", argv=["executor", "quiesce", "--exit-when-ready"]),
        dict(mode="error", argv=["executor", "quiesce"]),
        dict(mode="error", argv=["executor", "health"]),
    ]
    for mode in ("queued", "preparing", "unknown", "error", "nested_error"):
        values.append(dict(mode=mode, argv=["executor", "retry", "original", "--json"]))
    values.append(dict(mode="queued", argv=["executor", "retry", "original"]))
    for mode in ("eof", "malformed", "oversized", "identity", "publication"):
        values.append(dict(mode=mode, argv=["worker", "recap", "--json"], stdin=mode))
    values.append(
        dict(mode="timeout", direct=True, operation="retry", payload={"submission_id": "original"})
    )
    values.append(dict(mode="missing", argv=["executor", "retry", "original", "--json"]))
    values.append(
        dict(
            mode="unsupported",
            argv=["skill", "edit", "panels", "--description", "Refuse", "--json"],
        )
    )
    return values


class Peer:
    def __init__(self, root: Path, cases: list[dict[str, Any]]) -> None:
        self.path = root / "run" / "executor.sock"
        self.path.parent.mkdir(exist_ok=True)
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.bind(str(self.path))
        self.socket.listen()
        self.socket.settimeout(15)
        self.cases = cases
        self.requests: list[dict[str, Any]] = []
        self.errors: list[BaseException] = []
        self.thread = threading.Thread(target=self.serve)

    def serve(self) -> None:
        try:
            for case in self.cases:
                if case["mode"] in ("missing", "unsupported"):
                    continue
                with self.socket.accept()[0] as stream:
                    stream.settimeout(5)

                    def exact(size: int) -> bytes:
                        result = b""
                        while len(result) < size:
                            chunk = stream.recv(size - len(result))
                            if not chunk:
                                raise AssertionError("request frame truncated")
                            result += chunk
                        return result

                    size = struct.unpack("!I", exact(4))[0]
                    assert 0 < size <= 1024 * 1024
                    request = type_adapter(IpcRequest).validate_json(exact(size), strict=True)
                    wire = as_object(request)
                    assert request.response_deadline is not None
                    seconds = request.response_deadline - time.monotonic()
                    assert 0 < seconds <= (0.04 if case.get("direct") else 30.1)
                    wire["response_deadline"] = "shared-monotonic-budget"
                    self.requests.append(wire)
                    identity = request.request_id
                    reply: dict[str, Any] = dict(
                        request_id=identity,
                        result={"id": "ticket", "ok": True, "large": 9007199254741003},
                        error=None,
                        unconfirmed=None,
                    )
                    mode = case["mode"]
                    if mode == "text":
                        reply["result"] = {"text": "Artifact bytes\n"}
                    if mode == "health":
                        reply["result"] = dict(
                            identity=as_object(mac_identity("incarnation")),
                            process_id=123,
                            lease_accepted_at_mac_ms=1700000000000,
                            last_lease_failure=None,
                            refusal=None,
                            admission_stopped=False,
                            going_to_sleep=False,
                        )
                    if mode in ("error", "nested_error"):
                        error = dict(code="agent_forbidden", message="No access", detail={})
                        reply["error" if mode == "error" else "result"] = (
                            error if mode == "error" else {"error": error}
                        )
                    if mode in ("queued", "preparing", "unknown", "publication"):
                        reply["unconfirmed"] = dict(
                            request_id=identity,
                            submission_id="wrong"
                            if mode == "publication"
                            else request.payload.get("submission_id", identity),
                            publication="queued" if mode == "publication" else mode,
                            acceptance="unconfirmed",
                        )
                    if mode == "identity":
                        reply["request_id"] = "wrong"
                    if mode in ("eof", "timeout"):
                        if mode == "timeout":
                            time.sleep(0.08)
                        continue
                    body = json.dumps(reply).encode() if mode != "malformed" else b"{"
                    header = struct.pack("!I", 2**22 if mode == "oversized" else len(body))
                    # Split header and body to prove reads do not assume packet boundaries.
                    stream.sendall(header[:2])
                    stream.sendall(header[2:] + body[:3])
                    with contextlib.suppress(BrokenPipeError):
                        stream.sendall(body[3:])
        except BaseException as error:
            self.errors.append(error)

    def __enter__(self) -> Peer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.thread.join(20)
        self.socket.close()
        self.path.unlink()
        assert not self.thread.is_alive()
        assert not self.errors, self.errors


def normalize(value: Any, requests: list[dict[str, Any]]) -> Any:
    text = json.dumps(value)
    for request in requests:
        text = text.replace(request["request_id"], "request")
    return json.loads(text)


def output_fields(result: dict[str, Any]) -> dict[str, Any]:
    fields = dict(result)
    for channel in ("stdout", "stderr"):
        if channel not in fields:
            continue
        try:
            fields[channel] = json.loads(fields[channel])
        except json.JSONDecodeError:
            pass  # Non-JSON output remains exact text.
    return fields


def test_mac_cli_socket_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    cases = scenarios()
    # A short, newly created canonical root also fits Darwin's Unix socket limit.
    with tempfile.TemporaryDirectory(prefix="mac-cli-") as temporary:
        root = Path(temporary).resolve()
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PLAN_") and key not in homes
        }
        environment.update(homes)
        environment.update(
            PLAN_EXECUTOR_ROOT=str(root),
            PLAN_EXECUTOR_CHILD_ID="child",
            PLAN_EXECUTOR_CAPABILITY="capability",
            PLAN_TICKET_ID="ticket",
            PLAN_SERVER_URL="http://127.0.0.1:9",
            PLAN_TEST_MODE="1",
        )
        expected = []
        with monkeypatch.context() as patch, Peer(root, cases) as python:
            for key in tuple(os.environ):
                if key.startswith("PLAN_"):
                    patch.delenv(key)
            for key, value in environment.items():
                patch.setenv(key, value)
            for case in cases:
                patch.setenv(
                    "PLAN_EXECUTOR_ROOT",
                    str(root / "absent") if case["mode"] == "missing" else str(root),
                )
                if case.get("direct"):
                    patch.setattr(ipc_client, "CLI_RESULT_TIMEOUT_SECONDS", 0.03)
                    patch.setattr(ipc_client, "CLI_TRANSPORT_RESERVE_SECONDS", 0.02)
                    expected.append(
                        as_object(
                            ipc_client.request(IpcOperation(case["operation"]), case["payload"])
                        )
                    )
                else:
                    result = CliRunner().invoke(
                        main, case["argv"], input=case.get("stdin", ""), prog_name="panels"
                    )
                    expected.append(
                        dict(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)
                    )
        source = root / "input.json"
        source.write_text(json.dumps(dict(environment=environment, scenarios=cases)))
        with Peer(root, cases) as typescript:
            observed = run_node(
                "server/executor/mac/testing/cli_parity.ts", source, homes, timeout=40
            )
        expected_fields = [output_fields(value) for value in expected]
        observed_fields = [output_fields(value) for value in observed["results"]]
        for values in (expected_fields, observed_fields):
            large = values[0]["stdout"]["large"]
            assert type(large) is int and large == 9007199254741003

        accepted_native_errors = []
        peer_error = dict(code="agent_forbidden", message="No access", detail={})
        native_error = json.dumps(peer_error, separators=(",", ":"))
        for index, (case, reference, candidate) in enumerate(
            zip(cases, expected_fields, observed_fields, strict=True)
        ):
            if case["mode"] == "error" and case["argv"] in (
                ["executor", "quiesce"],
                ["executor", "health"],
            ):
                python_prefix = f"error: {peer_error}\n"
                native_prefix = f"error: {native_error}\n"
                assert reference["stderr"].startswith(python_prefix)
                assert candidate["stderr"].startswith(native_prefix)
                accepted_native_errors.append(
                    dict(
                        case=index,
                        field="stderr",
                        python=reference["stderr"],
                        typescript=candidate["stderr"],
                        reason="Native human errors use JSON rather than Python dictionary text",
                    )
                )
                candidate["stderr"] = python_prefix + candidate["stderr"][len(native_prefix) :]
            elif case["mode"] == "missing":
                python_message = "[Errno 2] No such file or directory"
                native_message = f"ENOENT: no such file or directory, lstat '{root / 'absent/run'}'"
                assert reference["stderr"] == dict(
                    error=dict(
                        code="connection",
                        message=python_message,
                        detail={},
                    )
                )
                assert candidate["stderr"] == dict(
                    error=dict(
                        code="connection",
                        message=native_message,
                        detail={},
                    )
                )
                accepted_native_errors.append(
                    dict(
                        case=index,
                        field="stderr.error.message",
                        python=python_message,
                        typescript=native_message,
                        reason="Native preconnection ENOENT text",
                    )
                )
                candidate["stderr"]["error"]["message"] = python_message
        assert len(accepted_native_errors) == 3
        assert normalize(expected_fields, python.requests) == normalize(
            observed_fields, typescript.requests
        )
        assert normalize(python.requests, python.requests) == normalize(
            typescript.requests, typescript.requests
        )
        assert len(python.requests) == len(cases) - 2
        report_direct(
            request,
            "unit22-mac-cli.json",
            observed,
            started,
            "real private Unix socket; JSON fields/exact integers, text/exits, absolute deadlines",
            cases=len(cases),
            requests=len(python.requests),
            accepted_native_errors=accepted_native_errors,
        )
