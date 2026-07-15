"""Unix-socket addressing and transport for Panels server lifecycle control."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from planner.server_lifecycle.contracts import (
    SERVER_CONTROL_PROTOCOL_VERSION,
    ServerControlRequest,
    ServerControlResponse,
    ServerRestartConnectionError,
    ServerRestartProtocolError,
)

_CONTROL_SOCKET_ENV: Final = "PLAN_SERVER_CONTROL_SOCKET"
_MAX_CONTROL_MESSAGE_BYTES: Final = 4096


def _server_lifecycle_directory() -> Path:
    directory = Path("/tmp") / f"panels-{os.getuid()}"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def resolve_server_control_socket_path(
    port: int,
    environ: Mapping[str, str],
    launch_root: Path,
) -> Path:
    explicit = environ.get(_CONTROL_SOCKET_ENV, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = launch_root / path
        return path.resolve(strict=False)
    return _server_lifecycle_directory() / f"server-{port}.sock"


def resolve_server_lifecycle_lease_path(port: int) -> Path:
    return _server_lifecycle_directory() / f"server-{port}.lock"


def encode_server_control_request(request: ServerControlRequest) -> bytes:
    return _encode_message({"version": request.version, "operation": request.operation})


def decode_server_control_request(payload: bytes) -> ServerControlRequest:
    message = _decode_message(payload)
    if set(message) != {"version", "operation"}:
        raise ServerRestartProtocolError("invalid control request shape")
    if (
        type(message["version"]) is not int
        or message["version"] != SERVER_CONTROL_PROTOCOL_VERSION
        or not isinstance(message["operation"], str)
        or message["operation"] != "restart"
    ):
        raise ServerRestartProtocolError("incompatible control request")
    return ServerControlRequest(
        version=SERVER_CONTROL_PROTOCOL_VERSION,
        operation="restart",
    )


def encode_server_control_response(response: ServerControlResponse) -> bytes:
    return _encode_message({"version": response.version, "status": response.status})


def decode_server_control_response(payload: bytes) -> ServerControlResponse:
    message = _decode_message(payload)
    if set(message) != {"version", "status"}:
        raise ServerRestartProtocolError("invalid control response shape")
    if (
        type(message["version"]) is not int
        or message["version"] != SERVER_CONTROL_PROTOCOL_VERSION
        or not isinstance(message["status"], str)
        or message["status"] != "accepted"
    ):
        raise ServerRestartProtocolError("incompatible control response")
    return ServerControlResponse(
        version=SERVER_CONTROL_PROTOCOL_VERSION,
        status="accepted",
    )


def request_server_restart(control_socket_path: Path) -> None:
    request = ServerControlRequest(
        version=SERVER_CONTROL_PROTOCOL_VERSION,
        operation="restart",
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(control_socket_path))
            client.sendall(encode_server_control_request(request))
            response = _receive_message(client)
            decode_server_control_response(response)
    except OSError as exc:
        raise ServerRestartConnectionError(
            f"Could not connect to the Panels supervisor at {control_socket_path}: {exc}"
        ) from exc
    except ServerRestartProtocolError as exc:
        raise ServerRestartProtocolError(
            f"Panels supervisor returned an incompatible response: {exc}"
        ) from exc


def _encode_message(message: dict[str, object]) -> bytes:
    return json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"


def _decode_message(payload: bytes) -> dict[str, Any]:
    if not payload.endswith(b"\n") or len(payload) > _MAX_CONTROL_MESSAGE_BYTES:
        raise ServerRestartProtocolError("control message must be one bounded JSON line")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServerRestartProtocolError("control message is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ServerRestartProtocolError("control message must be a JSON object")
    return decoded


def _receive_message(connection: socket.socket) -> bytes:
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = connection.recv(min(1024, _MAX_CONTROL_MESSAGE_BYTES + 1 - len(payload)))
        if not chunk:
            raise ServerRestartProtocolError("control connection closed before a complete reply")
        payload.extend(chunk)
        if len(payload) > _MAX_CONTROL_MESSAGE_BYTES:
            raise ServerRestartProtocolError("control reply exceeds the protocol limit")
    return bytes(payload)
