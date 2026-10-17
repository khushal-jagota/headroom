"""Synchronous CLI client for the executor's owner-only Unix socket."""

from __future__ import annotations

import json
import os
import socket
from uuid import uuid4

from pydantic import TypeAdapter

from planner.executor.configuration import (
    CLI_RESULT_TIMEOUT_SECONDS,
    EXECUTOR_CAPABILITY_ENV,
    EXECUTOR_CHILD_ID_ENV,
    executor_paths,
)
from planner.executor.contracts import IpcOperation, IpcRequest, IpcResponse, JsonObject


def request(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
    request = IpcRequest(
        str(uuid4()),
        operation,
        os.environ.get(EXECUTOR_CHILD_ID_ENV),
        os.environ.get(EXECUTOR_CAPABILITY_ENV),
        payload,
    )
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(CLI_RESULT_TIMEOUT_SECONDS + 5)
        stream.connect(str(executor_paths().ipc_socket))
        encoded = TypeAdapter(IpcRequest).dump_json(request)
        stream.sendall(len(encoded).to_bytes(4, "big") + encoded)

        def read_exact(length: int) -> bytes:
            data = bytearray()
            while len(data) < length:
                chunk = stream.recv(length - len(data))
                if not chunk:
                    raise ConnectionError("executor IPC closed without a response")
                data.extend(chunk)
            return bytes(data)

        length = int.from_bytes(read_exact(4), "big")
        if length > 1024 * 1024:
            raise ValueError("executor IPC response exceeds frame limit")
        return TypeAdapter(IpcResponse).validate_python(json.loads(read_exact(length)))
