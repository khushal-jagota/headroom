"""JSON control messages for the stored-script process."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import cast

from planner.measure_sources.contracts import FailureCode, JsonValue, RunFailure, RuntimeBudget


class Refusal(Exception):
    def __init__(self, failure: RunFailure) -> None:
        self.failure = failure
        super().__init__(failure.code.value)


def refuse(code: FailureCode, budget: RuntimeBudget | None = None) -> Refusal:
    return Refusal(RunFailure(code, budget))


def object_value(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise refuse(FailureCode.malformed_protocol)
    return cast(dict[str, JsonValue], value)


def string(value: object) -> str:
    if not isinstance(value, str):
        raise refuse(FailureCode.malformed_protocol)
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise refuse(FailureCode.malformed_protocol)
    return value


def decode(data: bytes) -> JsonValue:
    try:
        return cast(JsonValue, json.loads(data))
    except (ValueError, UnicodeError, RecursionError):
        raise refuse(FailureCode.malformed_protocol) from None


def _default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError("unsupported wire value")


def encode(value: object) -> bytes:
    return json.dumps(value, default=_default, allow_nan=False).encode() + b"\n"


def failure_from_wire(value: object) -> RunFailure:
    data = object_value(value)
    return RunFailure(
        code=FailureCode(string(data.get("code"))),
        budget=RuntimeBudget(string(data["budget"])) if data.get("budget") is not None else None,
        http_status=integer(data["http_status"]) if data.get("http_status") is not None else None,
        exception_type=string(data["exception_type"])
        if data.get("exception_type") is not None
        else None,
        source_line=integer(data["source_line"]) if data.get("source_line") is not None else None,
    )
