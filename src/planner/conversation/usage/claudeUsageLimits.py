"""Mechanical translation of T3 provider/Layers/claudeUsageLimits.ts (75e4ceb)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from .codexUsageLimits import isNumber, isoFromEpochSeconds
from .providerUsageLimits import clampPercent, makeUnavailableUsageLimits, makeUsageLimits

SESSION_MINS = 5 * 60
WEEK_MINS = 7 * 24 * 60
WINDOWS: dict[str, dict[str, Any]] = {
    "five_hour": {"kind": "session", "label": "Session", "windowDurationMins": SESSION_MINS},
    "seven_day": {"kind": "weekly", "label": "Weekly", "windowDurationMins": WEEK_MINS},
}
OVERAGE_INCLUDED_EVENT_TYPE = "seven_day_overage_included"


def makeClaudeScopedLimitNames() -> dict[str, Any]:
    return {"overageIncluded": None}


def scopedWindowId(displayName: str) -> str:
    return "seven_day_" + re.sub(r"[^a-z0-9]+", "_", displayName.lower())


def scopedWindow(displayName: str, usedPercent: float, resetsAt: str | None) -> dict[str, Any]:
    result = {
        "id": scopedWindowId(displayName),
        "kind": "weekly",
        "label": f"Weekly · {displayName}",
        "windowDurationMins": WEEK_MINS,
        "usedPercent": clampPercent(usedPercent),
    }
    if resetsAt:
        result["resetsAt"] = resetsAt
    return result


def readModelScoped(rateLimits: dict[str, Any]) -> list[dict[str, Any]]:
    raw = rateLimits.get("model_scoped")
    if not isinstance(raw, list):
        return []
    return [
        entry
        for entry in raw
        if isinstance(entry, dict) and isinstance(entry.get("display_name"), str)
    ]


def isoFromString(value: str | None) -> str | None:
    if not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return date.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def makeWindow(id: str, usedPercent: float, resetsAt: str | None) -> dict[str, Any]:
    result = {"id": id, **WINDOWS[id], "usedPercent": clampPercent(usedPercent)}
    if resetsAt:
        result["resetsAt"] = resetsAt
    return result


def claudeRateLimitEventToUpdate(
    info: dict[str, Any], names: dict[str, Any]
) -> dict[str, Any] | None:
    type = info.get("rateLimitType")
    if not type or not isNumber(info.get("utilization")):
        return None
    usedPercent = info["utilization"] * 100
    resetsAt = isoFromEpochSeconds(info.get("resetsAt"))
    if type in WINDOWS:
        return {"windows": [makeWindow(type, usedPercent, resetsAt)]}
    if type == OVERAGE_INCLUDED_EVENT_TYPE and names.get("overageIncluded"):
        return {"windows": [scopedWindow(names["overageIncluded"], usedPercent, resetsAt)]}
    return None


def claudeUsageResponseToLimits(input: dict[str, Any]) -> dict[str, Any]:
    response, checkedAt = input["response"], input["checkedAt"]
    if not response.get("rate_limits_available") or response.get("rate_limits") is None:
        return {
            "limits": makeUnavailableUsageLimits({"checkedAt": checkedAt, "reason": "unsupported"}),
            "names": {"overageIncluded": None},
        }
    windows = []
    for id in WINDOWS:
        window = response["rate_limits"].get(id)
        if not window or not isNumber(window.get("utilization")):
            continue
        windows.append(
            makeWindow(id, window["utilization"], isoFromString(window.get("resets_at")))
        )
    overageIncluded = None
    for entry in readModelScoped(response["rate_limits"]):
        if not isNumber(entry.get("utilization")):
            continue
        windows.append(
            scopedWindow(
                entry["display_name"], entry["utilization"], isoFromString(entry.get("resets_at"))
            )
        )
        if overageIncluded is None:
            overageIncluded = entry["display_name"]
    return {
        "limits": makeUsageLimits({"checkedAt": checkedAt, "windows": windows}),
        "names": {"overageIncluded": overageIncluded},
    }


def recordClaudeUsageResponse(namesRef: dict[str, Any], input: dict[str, Any]) -> dict[str, Any]:
    result = claudeUsageResponseToLimits(input)
    namesRef.clear()
    namesRef.update(result["names"])
    return result["limits"]  # type: ignore[no-any-return]
