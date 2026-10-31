"""Synchronous CLI client for the executor's owner-only Unix socket."""

from __future__ import annotations

import math
import os
import re
import socket
import time
from typing import Any
from uuid import uuid4

from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
    CLI_TRANSPORT_RESERVE_SECONDS,
    EXECUTOR_CAPABILITY_ENV,
    EXECUTOR_CHILD_ID_ENV,
    MAX_ENVELOPE_BYTES,
    executor_paths,
)
from planner.executor.protocol import (
    IpcOperation,
    IpcRequest,
    IpcResponse,
    IpcRetryPayload,
    IpcWritePayload,
    JsonObject,
    UnconfirmedCliResult,
    encode_frame,
    type_adapter,
)


def cli_mutation(method: str, path: str, body: Any) -> tuple[str, JsonObject]:
    """Translate a supported CLI route into a registry operation, never a proxy route."""
    if method == "POST" and path == "/api/feedback/use":
        return "feedback.use", {"body": body}
    blocker = re.fullmatch(r"/api/collections/blockers/([^/]+)/([^/]+)", path)
    if blocker and method in ("PUT", "DELETE"):
        return "ticket.block" if method == "PUT" else "ticket.unblock", {
            "ticket_id": blocker[1],
            "blocker_id": blocker[2],
            "body": {},
        }
    if method == "POST" and path == "/api/tickets":
        return "ticket.create", {"body": body}
    proposal = re.fullmatch(r"/api/tickets/([^/]+)/propose", path)
    patch = re.fullmatch(r"/api/tickets/([^/]+)", path)
    if method == "POST" and proposal:
        return "ticket.propose", {"ticket_id": proposal[1], "body": body}
    if method == "PATCH" and patch:
        return "ticket.edit", {"ticket_id": patch[1], "body": body}
    if method == "POST" and path == "/api/messages/send":
        return "message.send", {"body": body}
    help_match = re.fullmatch(r"/api/tickets/([^/]+)/request-help", path)
    if method == "POST" and help_match:
        return "ticket.request_help", {"ticket_id": help_match[1], "body": body}
    raise ValueError(f"This executor does not support {method} {path}")


def request(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
    deadline = time.monotonic() + CLI_RESULT_TIMEOUT_SECONDS
    request_id = str(uuid4())
    submission_id = request_id
    if operation is IpcOperation.write:
        type_adapter(IpcWritePayload).validate_python(payload)
    elif operation is IpcOperation.retry:
        submission_id = type_adapter(IpcRetryPayload).validate_python(payload).submission_id
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
            response = type_adapter(IpcResponse).validate_json(read_exact(length), strict=True)
            if response.request_id != request_id:
                raise ValueError("executor IPC response identity mismatch")
            if response.unconfirmed is not None and (
                response.unconfirmed.request_id != request_id
                or response.unconfirmed.submission_id != submission_id
            ):
                raise ValueError("executor IPC publication identity mismatch")
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
