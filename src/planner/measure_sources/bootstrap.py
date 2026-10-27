"""Run one trusted stored script with fixed data and named-key helpers."""

from __future__ import annotations

import json
import resource
import socket
import sys
import traceback
from typing import Any

import httpx

from planner.credentials.contracts import BasicSecret, BearerSecret, CredentialSecret, HeaderSecret
from planner.measure_sources.logic.transport import authentication_headers
from planner.measure_sources.transport import request as http_request

# Persist a useful error category and source line, never the exception's raw message.
EXCEPTION_NAMES = (
    KeyError,
    IndexError,
    TypeError,
    ValueError,
    AttributeError,
    NameError,
    ZeroDivisionError,
    ImportError,
    FileNotFoundError,
    PermissionError,
    RuntimeError,
    SyntaxError,
    MemoryError,
    AssertionError,
)


def main() -> None:
    control = socket.socket(fileno=int(sys.argv[1]))
    channel = control.makefile("rwb")

    def send(value: object) -> None:
        channel.write(json.dumps(value, allow_nan=False).encode() + b"\n")
        channel.flush()

    config = json.loads(channel.readline())
    context = config["context"]
    requests = 0
    response_bytes = 0

    def call(op: str, **values: Any) -> Any:
        send({"op": op, **values})
        reply = json.loads(channel.readline())
        if reply.get("failure") is not None:
            raise CredentialUnavailable() if op == "credential" else InvalidOutput()
        return reply.get("value")

    def request(
        url: str,
        credential_ref: str | dict[str, str] | None = None,
        method: str = "GET",
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        nonlocal requests, response_bytes
        resolved_headers = dict(headers or {})
        if credential_ref is not None:
            reference = (
                {"scheme": "local", "name": credential_ref}
                if isinstance(credential_ref, str)
                else credential_ref
            )
            value = call("credential", reference=reference)
            kind, data = value["kind"], value["secret"]
            secret: CredentialSecret
            if kind == "bearer":
                secret = BearerSecret(data["token"])
            elif kind == "basic":
                secret = BasicSecret(data["username"], data["password"])
            else:
                secret = HeaderSecret(data["header_name"], data["value"])
            resolved_headers.update(authentication_headers(secret))
        requests += 1
        response = http_request(
            url,
            method=method,
            json_body=json_body,
            headers=resolved_headers,
            timeout_seconds=config["timeout_seconds"],
        )
        response_bytes += len(response.content)
        return response

    def latest(measure_id: str) -> Any:
        return call("latest", measure_id=measure_id)

    def record(
        measure_id: str,
        date: str,
        value: float | None,
        unavailable_reason: str | None = None,
    ) -> None:
        call(
            "record",
            measure_id=measure_id,
            date=date,
            value=value,
            unavailable_reason=unavailable_reason,
        )

    failure: dict[str, Any] | None = None
    try:
        source = compile(config["script"], "<measure-source>", "exec")
        exec(
            source,
            {
                "__name__": "__main__",
                "context": context,
                "request": request,
                "latest": latest,
                "record": record,
            },
        )
    except BaseException as error:
        if not isinstance(error, SystemExit) or error.code not in (None, 0):
            code = "runtime_failure"
            if isinstance(error, CredentialUnavailable):
                code = "credential_unavailable"
            elif isinstance(error, InvalidOutput):
                code = "invalid_output"
            elif isinstance(error, httpx.HTTPStatusError):
                code = "http_error"
            elif isinstance(error, httpx.HTTPError):
                code = "transport_failure"
            elif isinstance(error, json.JSONDecodeError):
                code = "invalid_json"
            frames = traceback.extract_tb(error.__traceback__)
            lines = [frame.lineno for frame in frames if frame.filename == "<measure-source>"]
            line = (
                error.lineno if isinstance(error, SyntaxError) else (lines[-1] if lines else None)
            )
            exception_name = next(
                (kind.__name__ for kind in EXCEPTION_NAMES if type(error) is kind), None
            )
            failure = {"code": code, "exception_type": exception_name, "source_line": line}
            if isinstance(error, httpx.HTTPStatusError):
                failure["http_status"] = error.response.status_code
    usage = resource.getrusage(resource.RUSAGE_SELF)
    send(
        {
            "op": "done",
            "failure": failure,
            "request_count": requests,
            "response_bytes": response_bytes,
            "cpu_milliseconds": int((usage.ru_utime + usage.ru_stime) * 1000),
        }
    )
    channel.close()
    control.close()


class CredentialUnavailable(Exception):
    pass


class InvalidOutput(Exception):
    pass


if __name__ == "__main__":
    main()
