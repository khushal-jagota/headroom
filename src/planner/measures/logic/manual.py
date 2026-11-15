"""Manual request identity and arithmetic, independent of persistence."""

import hashlib
import json
from typing import cast

from planner.measures.contracts import ManualWriteBody, Observation
from planner.measures.logic.dates import calendar_date, invalid
from planner.measures.logic.formulas import number


def parse_write(raw: object) -> ManualWriteBody:
    if not isinstance(raw, dict):
        raise invalid("invalid manual measure write")
    common = {"request_id", "date", "expected_definition_revision", "basis", "operation"}
    operation = raw.get("operation")
    fields = (
        {"value", "expected_write_revision"}
        if operation == "replace"
        else {"delta"}
        if operation == "increment"
        else None
    )
    if fields is None or set(raw) != common | fields:
        raise invalid("invalid manual measure write")
    if not isinstance(raw["request_id"], str) or not raw["request_id"].strip():
        raise invalid("manual write requires a request ID")
    if not isinstance(raw["date"], str):
        raise invalid("invalid measure date")
    calendar_date(raw["date"])
    if raw["basis"] not in ("date", "latest"):
        raise invalid("invalid manual write basis")
    if (
        type(raw["expected_definition_revision"]) is not int
        or raw["expected_definition_revision"] < 1
    ):
        raise invalid("measure definition revision is required")
    normalized = dict(raw)
    if operation == "replace":
        if type(raw["expected_write_revision"]) is not int or raw["expected_write_revision"] < 0:
            raise invalid("manual write revision is required")
        normalized["value"] = number(raw["value"])
    else:
        normalized["delta"] = number(raw["delta"])
    return cast(ManualWriteBody, normalized)


def fingerprint(measure_id: str, body: ManualWriteBody) -> str:
    payload = json.dumps(
        {"measure_id": measure_id, **body}, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def accepted_value(body: ManualWriteBody, base: Observation | None) -> float:
    if body["operation"] == "replace":
        return number(body["value"])
    return number((base.value if base is not None else 0) + body["delta"])
