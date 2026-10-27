"""Strict bounded JSON parsing and fixed safe failures; no provider diagnostics."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import cast

from planner.measure_sources.contracts import (
    FailureCode,
    JsonValue,
    RunFailure,
    RuntimeBudget,
)


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


def _unique(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode(data: bytes, *, depth: int = 32) -> JsonValue:
    # Check structural depth before json.loads can exhaust its recursion stack.
    level = 0
    quoted = False
    escaped = False
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            level += 1
            if level > depth:
                raise refuse(FailureCode.malformed_protocol)
        elif byte in (93, 125):
            level -= 1
    try:
        value: JsonValue = json.loads(data, object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        raise refuse(FailureCode.malformed_protocol) from None
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise refuse(FailureCode.malformed_protocol)
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return value


def _default(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError("unsupported wire value")


def encode(value: object) -> bytes:
    return (
        json.dumps(value, default=_default, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    )


def failure_from_wire(value: object) -> RunFailure:
    data = object_value(value)
    return RunFailure(
        FailureCode(string(data.get("code"))),
        RuntimeBudget(string(data["budget"])) if data.get("budget") is not None else None,
        integer(data["http_status"]) if data.get("http_status") is not None else None,
    )
