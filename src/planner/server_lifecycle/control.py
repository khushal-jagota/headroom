"""Unix-socket addressing and transport for Panels server lifecycle control."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from planner.platform_runtime.selection import get_platform_adapter
from planner.server_lifecycle.configuration import (
    EVENT_LOOP_TASKS_CLIENT_TIMEOUT_SECONDS,
    MAX_EVENT_LOOP_TASKS_RESPONSE_BYTES,
)
from planner.server_lifecycle.contracts import (
    SERVER_CONTROL_PROTOCOL_VERSION,
    EventLoopTask,
    EventLoopTaskFrame,
    EventLoopTasksConnectionError,
    EventLoopTasksProtocolError,
    EventLoopTasksRequest,
    EventLoopTasksResponse,
    EventLoopTasksTimeoutError,
    ServerControlRequest,
    ServerControlResponse,
    ServerRestartConnectionError,
    ServerRestartProtocolError,
)

_CONTROL_SOCKET_ENV: Final = "PLAN_SERVER_CONTROL_SOCKET"
_MAX_CONTROL_MESSAGE_BYTES: Final = 4096
# The same bound, for the event-loop-tasks socket's request reader in the application.
MAX_CONTROL_REQUEST_BYTES: Final = _MAX_CONTROL_MESSAGE_BYTES


def resolve_server_control_socket_path(
    port: int, environ: Mapping[str, str], launch_root: Path
) -> Path:
    return get_platform_adapter().control_socket_path(port, environ, launch_root)


def resolve_event_loop_tasks_socket_path(control_socket_path: Path) -> Path:
    return get_platform_adapter().event_loop_tasks_socket_path(control_socket_path)


def resolve_server_lifecycle_lease_path(port: int) -> Path:
    return get_platform_adapter().lifecycle_lease_path(port)


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
            response = receive_server_control_message(client.recv)
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


def receive_server_control_message(receive: Callable[[int], bytes]) -> bytes:
    """Receive one bounded newline-framed control message through a caller-owned read."""
    payload = bytearray()
    while not payload.endswith(b"\n"):
        chunk = receive(_MAX_CONTROL_MESSAGE_BYTES + 1 - len(payload))
        if not chunk:
            raise ServerRestartProtocolError("control connection closed before a complete message")
        payload.extend(chunk)
        if len(payload) > _MAX_CONTROL_MESSAGE_BYTES:
            raise ServerRestartProtocolError("control message exceeds the protocol limit")
    return bytes(payload)


def encode_event_loop_tasks_request(request: EventLoopTasksRequest) -> bytes:
    return _encode_message({"version": request.version, "operation": request.operation})


def decode_event_loop_tasks_request(payload: bytes) -> EventLoopTasksRequest:
    try:
        message = _decode_message(payload)
    except ServerRestartProtocolError as exc:
        raise EventLoopTasksProtocolError(str(exc)) from exc
    if (
        set(message) != {"version", "operation"}
        or type(message["version"]) is not int
        or message["version"] != SERVER_CONTROL_PROTOCOL_VERSION
        or message["operation"] != "list_event_loop_tasks"
    ):
        raise EventLoopTasksProtocolError("incompatible event loop tasks request")
    return EventLoopTasksRequest(
        version=SERVER_CONTROL_PROTOCOL_VERSION,
        operation="list_event_loop_tasks",
    )


def encode_event_loop_task(task: EventLoopTask) -> bytes:
    return json.dumps(
        {
            "name": task.name,
            "coroutine": task.coroutine,
            "age_seconds": task.age_seconds,
            "stack": [
                {"file": frame.file, "line": frame.line, "function": frame.function}
                for frame in task.stack
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def encode_event_loop_tasks_response(
    *,
    total_task_count: int,
    omitted_task_count: int,
    encoded_tasks: list[bytes],
) -> bytes:
    """Join tasks already encoded by `encode_event_loop_task` into one newline-ended answer."""
    head = json.dumps(
        {
            "version": SERVER_CONTROL_PROTOCOL_VERSION,
            "total_task_count": total_task_count,
            "omitted_task_count": omitted_task_count,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return head[:-1] + b',"tasks":[' + b",".join(encoded_tasks) + b"]}\n"


# An upper bound on everything but the tasks, with the widest counts. Reserving it means an
# answer that would have fit exactly with its real counts can leave out one more task.
EVENT_LOOP_TASKS_RESPONSE_ENVELOPE_BYTES: Final = len(
    encode_event_loop_tasks_response(
        total_task_count=2**63, omitted_task_count=2**63, encoded_tasks=[]
    )
)


def decode_event_loop_tasks_response(payload: bytes) -> EventLoopTasksResponse:
    if not payload.endswith(b"\n") or len(payload) > MAX_EVENT_LOOP_TASKS_RESPONSE_BYTES:
        raise EventLoopTasksProtocolError("event loop tasks answer must be one bounded JSON line")
    try:
        message = json.loads(payload)
        if (
            not isinstance(message, dict)
            or set(message) != {"version", "total_task_count", "omitted_task_count", "tasks"}
            or message["version"] != SERVER_CONTROL_PROTOCOL_VERSION
        ):
            raise EventLoopTasksProtocolError("incompatible event loop tasks answer")
        return EventLoopTasksResponse(
            version=SERVER_CONTROL_PROTOCOL_VERSION,
            total_task_count=_integer(message["total_task_count"]),
            omitted_task_count=_integer(message["omitted_task_count"]),
            tasks=tuple(
                EventLoopTask(
                    name=_text(entry["name"]),
                    coroutine=_text(entry["coroutine"]),
                    age_seconds=None if entry["age_seconds"] is None else _seconds(entry),
                    stack=tuple(
                        EventLoopTaskFrame(
                            file=_text(frame["file"]),
                            line=None if frame["line"] is None else _integer(frame["line"]),
                            function=_text(frame["function"]),
                        )
                        for frame in entry["stack"]
                    ),
                )
                for entry in message["tasks"]
            ),
        )
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise EventLoopTasksProtocolError(f"malformed event loop tasks answer: {exc!r}") from exc


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected text, got {value!r}")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"expected an integer, got {value!r}")
    return value


def _seconds(entry: dict[str, Any]) -> float:
    value = entry["age_seconds"]
    if type(value) not in {int, float}:
        raise TypeError(f"expected seconds, got {value!r}")
    return float(value)


def request_event_loop_tasks(tasks_socket_path: Path) -> EventLoopTasksResponse:
    """Ask the application process for its event-loop tasks. Reads only; changes nothing."""
    request = EventLoopTasksRequest(
        version=SERVER_CONTROL_PROTOCOL_VERSION,
        operation="list_event_loop_tasks",
    )
    payload = bytearray()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(EVENT_LOOP_TASKS_CLIENT_TIMEOUT_SECONDS)
        try:
            client.connect(str(tasks_socket_path))
        except TimeoutError as exc:
            raise _no_answer_in_time(tasks_socket_path) from exc
        except OSError as exc:
            raise EventLoopTasksConnectionError(
                f"Could not connect to the Panels application at {tasks_socket_path}: {exc}"
            ) from exc
        try:
            client.sendall(encode_event_loop_tasks_request(request))
            while chunk := client.recv(65536):
                payload.extend(chunk)
                if len(payload) > MAX_EVENT_LOOP_TASKS_RESPONSE_BYTES:
                    raise EventLoopTasksProtocolError(
                        "event loop tasks answer exceeds the protocol limit"
                    )
        except TimeoutError as exc:
            raise _no_answer_in_time(tasks_socket_path) from exc
        except OSError as exc:
            raise EventLoopTasksProtocolError(
                f"The Panels application at {tasks_socket_path} dropped the exchange: {exc}"
            ) from exc
    return decode_event_loop_tasks_response(bytes(payload))


def _no_answer_in_time(tasks_socket_path: Path) -> EventLoopTasksTimeoutError:
    return EventLoopTasksTimeoutError(
        f"The Panels application at {tasks_socket_path} did not answer within "
        f"{EVENT_LOOP_TASKS_CLIENT_TIMEOUT_SECONDS:g} seconds. Its event loop may be blocked "
        "by synchronous code; a native stack dump of the application process shows where."
    )
