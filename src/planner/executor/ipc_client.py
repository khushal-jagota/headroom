"""Synchronous CLI client for the executor's owner-only Unix socket."""

from __future__ import annotations

import json
import math
import os
import socket
import time
from uuid import uuid4

from pydantic import TypeAdapter

from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
    CLI_TRANSPORT_RESERVE_SECONDS,
    EXECUTOR_CAPABILITY_ENV,
    EXECUTOR_CHILD_ID_ENV,
    MAX_ENVELOPE_BYTES,
    executor_paths,
)
from planner.executor.contracts import (
    IpcOperation,
    IpcRequest,
    IpcResponse,
    IpcRetryPayload,
    IpcWritePayload,
    JsonObject,
    UnconfirmedCliResult,
)
from planner.executor.wire import encode_frame


def request(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
    if operation in (IpcOperation.catalog_diagnostic_arm, IpcOperation.catalog_diagnostic_result):
        from uuid import UUID

        from planner.executor.catalog_diagnostics import arm_request, bounded_payload
        from planner.executor.contracts import CatalogDiagnosticResult

        if (
            os.environ.get(EXECUTOR_CHILD_ID_ENV) is not None
            or os.environ.get(EXECUTOR_CAPABILITY_ENV) is not None
        ):
            raise ValueError("catalog diagnostic operations require the owner socket")
        bounded_payload(payload)
        if operation is IpcOperation.catalog_diagnostic_arm:
            arm_request(payload)
        else:
            UUID(
                TypeAdapter(CatalogDiagnosticResult)
                .validate_json(json.dumps(payload), strict=True)
                .request_id
            )
    deadline = time.monotonic() + CLI_RESULT_TIMEOUT_SECONDS
    request_id = str(uuid4())
    submission_id = request_id
    if operation is IpcOperation.write:
        TypeAdapter(IpcWritePayload).validate_python(payload)
    elif operation is IpcOperation.retry:
        submission_id = TypeAdapter(IpcRetryPayload).validate_python(payload).submission_id
    ipc_request = IpcRequest(
        request_id,
        operation,
        os.environ.get(EXECUTOR_CHILD_ID_ENV),
        os.environ.get(EXECUTOR_CAPABILITY_ENV),
        payload,
        deadline,
    )
    # Serialization and the frame limit fail before emission is possible.
    encoded = encode_frame(ipc_request)
    transport_deadline = deadline + CLI_TRANSPORT_RESERVE_SECONDS
    emission_possible = False
    validated_response: IpcResponse | None = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:

            def remaining() -> None:
                seconds = transport_deadline - time.monotonic()
                if not math.isfinite(seconds) or seconds <= 0:
                    raise TimeoutError("executor IPC response deadline expired")
                stream.settimeout(seconds)

            remaining()
            stream.connect(str(executor_paths().ipc_socket))
            remaining()
            # sendall can fail after a partial emission. Never infer refusal from it.
            emission_possible = True
            stream.sendall(encoded)

            def read_exact(length: int) -> bytes:
                data = bytearray()
                while len(data) < length:
                    remaining()
                    chunk = stream.recv(length - len(data))
                    if not chunk:
                        raise ConnectionError("executor IPC closed without a response")
                    data.extend(chunk)
                return bytes(data)

            length = int.from_bytes(read_exact(4), "big")
            if not 0 < length <= MAX_ENVELOPE_BYTES:
                raise ValueError("executor IPC response exceeds frame limit")
            response = TypeAdapter(IpcResponse).validate_json(read_exact(length), strict=True)
            if response.request_id != request_id:
                raise ValueError("executor IPC response identity mismatch")
            if response.unconfirmed is not None and (
                response.unconfirmed.request_id != request_id
                or response.unconfirmed.submission_id != submission_id
            ):
                raise ValueError("executor IPC publication identity mismatch")
            if response.queued is not None and response.queued.submission_id != submission_id:
                raise ValueError("executor IPC queued identity mismatch")
            validated_response = response
            return response
    except (OSError, ValueError) as error:
        if validated_response is not None:
            return validated_response
        if emission_possible and operation in (IpcOperation.write, IpcOperation.retry):
            return IpcResponse(
                request_id,
                None,
                None,
                unconfirmed=UnconfirmedCliResult(request_id, submission_id, "unknown"),
            )
        raise error
