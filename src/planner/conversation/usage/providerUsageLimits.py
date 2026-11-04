"""Mechanical translation of T3 provider/providerUsageLimits.ts (75e4ceb)."""

from __future__ import annotations

import math
from typing import Any

WINDOW_KIND_ORDER = {"session": 0, "weekly": 1, "monthly": 2, "other": 3}


def clampPercent(value: float) -> float:
    return max(0, min(100, value)) if math.isfinite(value) else 0


def sortWindows(windows: Any) -> list[dict[str, Any]]:
    return sorted(windows, key=lambda window: (WINDOW_KIND_ORDER[window["kind"]], window["id"]))


def makeUsageLimits(input: dict[str, Any]) -> dict[str, Any]:
    return {"checkedAt": input["checkedAt"], "windows": sortWindows(input["windows"])}


def makeUnavailableUsageLimits(input: dict[str, Any]) -> dict[str, Any]:
    unavailable = {"reason": input["reason"]}
    if input.get("message"):
        unavailable["message"] = input["message"]
    return {"checkedAt": input["checkedAt"], "windows": [], "unavailable": unavailable}


def applyUsageLimitsUpdate(input: dict[str, Any]) -> dict[str, Any] | None:
    previous = input.get("previous")
    update = input["update"]
    if (
        not update["windows"]
        or (previous or {}).get("unavailable", {}).get("reason") == "unsupported"
    ):
        return previous
    merged = {window["id"]: window for window in (previous or {}).get("windows", [])}
    changed = False
    for window in update["windows"]:
        existing = merged.get(window["id"])
        next = {**window, "usedPercent": clampPercent(window["usedPercent"])}
        for field in ("resetsAt", "windowDurationMins"):
            if field not in window and existing is not None and field in existing:
                next[field] = existing[field]
        if existing is None or not usageWindowEquals(existing, next):
            merged[window["id"]] = next
            changed = True
    if not changed and previous is not None and "unavailable" not in previous:
        return previous  # type: ignore[no-any-return]
    result = makeUsageLimits({"checkedAt": input["checkedAt"], "windows": merged.values()})
    if previous is not None and "resetCredits" in previous:
        result["resetCredits"] = previous["resetCredits"]
    return result


def usageWindowEquals(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(
        a.get(field) == b.get(field)
        for field in (
            "id",
            "kind",
            "label",
            "usedPercent",
            "resetsAt",
            "windowDurationMins",
        )
    )


def resolveUsageLimitsAfterProbe(input: dict[str, Any]) -> dict[str, Any] | None:
    published, probed = input.get("published"), input.get("probed")
    if (
        (probed or {}).get("unavailable", {}).get("reason") == "probeFailed"
        and published
        and "unavailable" not in published
    ):
        return published  # type: ignore[no-any-return]
    return probed
