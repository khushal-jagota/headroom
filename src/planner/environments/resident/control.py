"""Owner-only executor IPC, using the shared executor envelopes and payload types."""

from __future__ import annotations

import json
import math
import os
import socket
import stat
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from planner.environments.resident.contracts import Compatibility, ResidentError, ResidentPackage
from planner.environments.resident.logic.update import fresh_health
from planner.executor.configuration import (
    MAX_ENVELOPE_BYTES,
    PROTOCOL_VERSION,
    SPOOL_FORMAT_VERSION,
)
from planner.executor.contracts import (
    ExecutorHealth,
    IpcOperation,
    IpcRequest,
    IpcResponse,
    JsonObject,
    QuiescenceReceipt,
    QuiesceRequest,
)
from planner.executor.preflight import check_spool_compatibility


class OwnerExecutorControl:
    """Never inherits a child's capability or sends an unbounded IPC request.

    Compatibility uses the executor-owned nonmutating spool inspection API.
    Inspection never migrates state or grants authority to replace source code.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout_seconds: float,
        before_quiesce: Callable[[QuiesceRequest], None] | None = None,
        compatibility_check: Callable[[ResidentPackage], Compatibility] | None = None,
        executor_root: Path | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ResidentError("IPC deadline must be positive")
        self.socket_path = socket_path
        self.executor_root = executor_root or socket_path.parent.parent
        if self.executor_root.absolute() != socket_path.parent.parent.absolute():
            raise ResidentError("Executor data root must match the owner-only socket root")
        self.timeout_seconds = timeout_seconds
        self.before_quiesce = before_quiesce
        self.compatibility_check = compatibility_check

    def compatible(self, package: ResidentPackage) -> Compatibility:
        if (
            package.protocol_version != PROTOCOL_VERSION
            or package.spool_format_version != SPOOL_FORMAT_VERSION
        ):
            return "incompatible"
        if self.compatibility_check is not None:
            return self.compatibility_check(package)
        try:
            check_spool_compatibility(self.executor_root, package.spool_format_version)
        except ValueError:
            return "incompatible"
        except OSError:
            return "unknown"
        return "compatible"

    def _owner_socket(self) -> None:
        for parent in (self.socket_path.parent, *self.socket_path.parent.parents):
            if parent.is_symlink():
                raise ResidentError("Executor socket path contains a symbolic link")
        directory = self.socket_path.parent.stat()
        endpoint = self.socket_path.lstat()
        if (
            directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
            or not stat.S_ISSOCK(endpoint.st_mode)
            or endpoint.st_uid != os.getuid()
            or stat.S_IMODE(endpoint.st_mode) != 0o600
        ):
            raise ResidentError("Executor IPC requires owner-only directory and socket")

    def _request(
        self, operation: IpcOperation, payload: JsonObject, *, timeout_seconds: float | None = None
    ) -> JsonObject:
        self._owner_socket()
        request = IpcRequest(str(uuid4()), operation, None, None, payload)
        encoded = TypeAdapter(IpcRequest).dump_json(request)
        if len(encoded) > MAX_ENVELOPE_BYTES:
            raise ResidentError("Executor IPC request exceeds frame limit")
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:

            def remaining() -> float:
                value = deadline - time.monotonic()
                if value <= 0:
                    raise TimeoutError("Executor IPC deadline expired")
                return value

            stream.settimeout(remaining())
            stream.connect(str(self.socket_path))
            stream.settimeout(remaining())
            stream.sendall(len(encoded).to_bytes(4, "big") + encoded)

            def read_exact(length: int) -> bytes:
                output = bytearray()
                while len(output) < length:
                    stream.settimeout(remaining())
                    chunk = stream.recv(length - len(output))
                    if not chunk:
                        raise ResidentError("Executor closed IPC before the complete response")
                    output.extend(chunk)
                return bytes(output)

            length = int.from_bytes(read_exact(4), "big")
            if not 0 < length <= MAX_ENVELOPE_BYTES:
                raise ResidentError("Executor IPC response exceeds frame limit")
            response = TypeAdapter(IpcResponse).validate_json(read_exact(length), strict=True)
        if response.request_id != request.request_id:
            raise ResidentError("Executor IPC response request identity mismatch")
        if response.error is not None or response.queued is not None or response.result is None:
            raise ResidentError(f"Executor IPC refused {operation}: {response.error}")
        return response.result

    def observe(self) -> ExecutorHealth | None:
        return self._observe(self.timeout_seconds)

    def _observe(self, timeout_seconds: float) -> ExecutorHealth | None:
        try:
            payload = self._request(IpcOperation.health, {}, timeout_seconds=timeout_seconds)
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        return TypeAdapter(ExecutorHealth).validate_json(json.dumps(payload), strict=True)

    def wait_for_health(
        self,
        package: ResidentPackage,
        *,
        not_before_ms: int,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> ExecutorHealth:
        if (
            not math.isfinite(timeout_seconds)
            or not math.isfinite(poll_seconds)
            or timeout_seconds <= 0
            or poll_seconds <= 0
        ):
            raise ResidentError("Health readiness deadline must be positive")
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResidentError("Executor readiness deadline expired")
            health = self._observe(min(remaining, self.timeout_seconds))
            if fresh_health(
                health, package, not_before_ms=not_before_ms, now_ms=time.time_ns() // 1_000_000
            ):
                assert health is not None
                return health
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))

    def quiesce(self, request: QuiesceRequest) -> QuiescenceReceipt:
        if self.before_quiesce is not None:
            self.before_quiesce(request)
        payload: JsonObject = TypeAdapter(JsonObject).validate_python(
            TypeAdapter(QuiesceRequest).dump_python(request, mode="json")
        )
        receipt = TypeAdapter(QuiescenceReceipt).validate_json(
            json.dumps(self._request(IpcOperation.quiesce, payload)), strict=True
        )
        if (
            receipt.identity != request.expected_identity
            or receipt.process_id != request.expected_process_id
        ):
            raise ResidentError("Executor quiescence receipt identity/process mismatch")
        return receipt
