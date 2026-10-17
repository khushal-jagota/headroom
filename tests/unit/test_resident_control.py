"""Real local socket exercises owner-only framing and restart identity refusal."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from planner.environments.resident.contracts import (
    ExecutorSourceClosure,
    ResidentError,
    ResidentPackage,
)
from planner.environments.resident.control import OwnerExecutorControl
from planner.executor.contracts import (
    ConnectionState,
    EnvelopeKind,
    ExecutorHealth,
    ExecutorIdentity,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    JsonObject,
    Quiescence,
    QuiescenceReceipt,
    QuiesceRequest,
)
from planner.executor.journal import FilesystemSpool

IDENTITY = ExecutorIdentity(1, "revision", 1, "incarnation", 2)


def encoded_object(value: object) -> JsonObject:
    return TypeAdapter(JsonObject).validate_python(TypeAdapter(type(value)).dump_python(value))


@contextmanager
def server(
    tmp_path: Path, respond: Callable[[IpcRequest], IpcResponse]
) -> Iterator[tuple[OwnerExecutorControl, list[IpcRequest]]]:
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    endpoint = run / "executor.sock"
    observed: list[IpcRequest] = []
    failures: list[BaseException] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        endpoint.chmod(0o600)
        listener.listen(1)
        listener.settimeout(2)

        def handle() -> None:
            try:
                with listener.accept()[0] as stream:
                    stream.settimeout(2)
                    length = int.from_bytes(stream.recv(4), "big")
                    data = bytearray()
                    while len(data) < length:
                        data.extend(stream.recv(length - len(data)))
                    request = TypeAdapter(IpcRequest).validate_json(data)
                    observed.append(request)
                    response = TypeAdapter(IpcResponse).dump_json(respond(request))
                    stream.sendall(len(response).to_bytes(4, "big") + response)
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=handle)
        thread.start()
        try:
            yield OwnerExecutorControl(endpoint, timeout_seconds=1), observed
        finally:
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert failures == []


def test_health_uses_owner_envelope_not_child_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAN_EXECUTOR_CHILD_ID", "must-not-inherit")
    monkeypatch.setenv("PLAN_EXECUTOR_CAPABILITY", "must-not-inherit")
    health = ExecutorHealth(IDENTITY, 42, ConnectionState.ready, True, 100, None)
    with server(
        tmp_path, lambda req: IpcResponse(req.request_id, encoded_object(health), None)
    ) as (control, requests):
        assert control.observe() == health
    assert requests[0].operation == IpcOperation.health
    assert requests[0].child_id is None
    assert requests[0].capability is None


@pytest.mark.parametrize("replacement", ["pid", "identity"])
def test_quiesce_rejects_replacement_receipt(tmp_path: Path, replacement: str) -> None:
    other = ExecutorIdentity(1, "revision", 1, "replacement", 2)
    receipt = QuiescenceReceipt(
        other if replacement == "identity" else IDENTITY,
        43 if replacement == "pid" else 42,
        Quiescence(True, 0, 0, 0, 0, True),
        True,
    )
    with server(
        tmp_path, lambda req: IpcResponse(req.request_id, encoded_object(receipt), None)
    ) as (control, requests):
        with pytest.raises(ResidentError, match="identity/process mismatch"):
            control.quiesce(QuiesceRequest(IDENTITY, 42, True))
    assert requests[0].operation == IpcOperation.quiesce
    assert requests[0].payload["expected_process_id"] == 42


def test_rejects_uncorrelated_response(tmp_path: Path) -> None:
    with server(tmp_path, lambda _: IpcResponse("other-request", {}, None)) as (control, _):
        with pytest.raises(ResidentError, match="request identity mismatch"):
            control.observe()


def test_rejects_non_private_socket_directory(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir(mode=0o755)
    endpoint = run / "executor.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.bind(str(endpoint))
        endpoint.chmod(0o600)
        with pytest.raises(ResidentError, match="owner-only"):
            OwnerExecutorControl(endpoint, timeout_seconds=1).observe()


def test_missing_socket_is_unavailable(tmp_path: Path) -> None:
    assert OwnerExecutorControl(tmp_path / "missing", timeout_seconds=1).observe() is None


def package() -> ResidentPackage:
    return ResidentPackage(
        "a" * 40,
        1,
        1,
        "Darwin",
        "arm64",
        "3.12",
        ExecutorSourceClosure(("entry.py",), "entry", "requirements.txt"),
    )


def tree(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }


def test_default_compatibility_inspects_real_root_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    try:
        spool.mint_envelope(
            conversation_id="conversation", kind=EnvelopeKind.cli_write, payload={"note": "pending"}
        )
        before = tree(root)
        control = OwnerExecutorControl(root / "run/executor.sock", timeout_seconds=1)
        assert control.compatible(package()) == "compatible"
        assert tree(root) == before
    finally:
        spool.close()


def test_default_checker_receives_executor_root_not_spool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, int]] = []

    def checker(root: Path, expected_format: int) -> None:
        calls.append((root, expected_format))

    monkeypatch.setattr("planner.environments.resident.control.check_spool_compatibility", checker)
    root = tmp_path / "executor"
    control = OwnerExecutorControl(root / "run/executor.sock", timeout_seconds=1)
    assert control.compatible(package()) == "compatible"
    assert calls == [(root, 1)]
    assert not root.exists()


@pytest.mark.parametrize(
    "failure, verdict",
    [(ValueError("unknown"), "incompatible"), (OSError("unreadable"), "unknown")],
)
def test_default_compatibility_refuses_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception, verdict: str
) -> None:
    def checker(root: Path, expected_format: int) -> None:
        raise failure

    monkeypatch.setattr("planner.environments.resident.control.check_spool_compatibility", checker)
    root = tmp_path / "executor"
    control = OwnerExecutorControl(root / "run/executor.sock", timeout_seconds=1)
    assert control.compatible(package()) == verdict
    assert not root.exists()


def test_default_compatibility_rejects_unknown_real_spool_preserving_bytes(tmp_path: Path) -> None:
    root = tmp_path / "executor"
    (root / "spool").mkdir(parents=True)
    (root / "spool/unknown.data").write_bytes(b"never migrate or delete")
    before = tree(root)
    control = OwnerExecutorControl(root / "run/executor.sock", timeout_seconds=1)
    assert control.compatible(package()) == "incompatible"
    assert tree(root) == before
