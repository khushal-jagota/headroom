"""Mechanical translation of T3 provider/Layers/codexUsageLimits.ts (75e4ceb)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, TypeGuard

from .providerUsageLimits import clampPercent, makeUsageLimits

SESSION_MINS = 5 * 60
WEEK_MINS = 7 * 24 * 60
MONTH_MINS = 30 * 24 * 60


def isNumber(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def isoFromEpochSeconds(value: Any) -> str | None:
    if not isNumber(value) or not math.isfinite(value) or value <= 0:
        return None
    try:
        return (
            datetime.fromtimestamp(value, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (ValueError, OverflowError, OSError):
        return None


def kindForDuration(mins: float) -> str:
    if mins >= MONTH_MINS:
        return "monthly"
    if mins >= WEEK_MINS:
        return "weekly"
    return "session"


def labelForKind(kind: str) -> str:
    return "Session" if kind == "session" else "Weekly" if kind == "weekly" else "Monthly"


def codexRateLimitsToWindows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    if snapshot.get("limitId") and snapshot["limitId"] != "codex":
        return []
    isMonthlyPlan = snapshot.get("planType") in ("free", "go")
    positions = [
        ("primary", snapshot.get("primary"), MONTH_MINS if isMonthlyPlan else SESSION_MINS),
        ("secondary", snapshot.get("secondary"), WEEK_MINS),
    ]
    windows = []
    for id, window, fallbackMins in positions:
        if (
            not window
            or not isNumber(window.get("usedPercent"))
            or not math.isfinite(window["usedPercent"])
        ):
            continue
        duration = window.get("windowDurationMins")
        windowDurationMins = duration if isNumber(duration) else fallbackMins
        kind = kindForDuration(windowDurationMins)
        resetsAt = isoFromEpochSeconds(window.get("resetsAt"))
        result = {
            "id": id,
            "kind": kind,
            "label": labelForKind(kind),
            "usedPercent": clampPercent(window["usedPercent"]),
            "windowDurationMins": windowDurationMins,
        }
        if resetsAt:
            result["resetsAt"] = resetsAt
        windows.append(result)
    return windows


def codexResetCreditsToContract(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    expiries = [
        credit.get("expiresAt")
        for credit in summary.get("credits", [])
        if credit.get("status") == "available" and isNumber(credit.get("expiresAt"))
    ]
    nextExpiresAt = isoFromEpochSeconds(min(expiries)) if expiries else None
    result = {"availableCount": max(0, summary["availableCount"])}
    if nextExpiresAt:
        result["nextExpiresAt"] = nextExpiresAt
    return result


def codexRateLimitsToLimits(input: dict[str, Any]) -> dict[str, Any]:
    resetCredits = codexResetCreditsToContract(input.get("resetCredits"))
    snapshots = input.get("rateLimitsByLimitId") or {}
    main = snapshots.get("codex")
    windows = codexRateLimitsToWindows(input["snapshot"] if main is None else main)
    result = makeUsageLimits({"checkedAt": input["checkedAt"], "windows": windows})
    if resetCredits is not None:
        result["resetCredits"] = resetCredits
    return result


def codexRateLimitsToUpdate(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    windows = codexRateLimitsToWindows(snapshot)
    return {"windows": windows} if windows else None


def codexRateLimitsFailureMessage(error: Any) -> str:
    tag = error.get("_tag") if isinstance(error, dict) else getattr(error, "_tag", None)
    if tag == "CodexAppServerRequestError":
        code = error.get("code") if isinstance(error, dict) else error.code
        return f"Codex could not read usage (JSON-RPC {code})."
    if tag == "CodexAppServerSpawnError":
        return "Codex could not be started to read usage."
    if tag == "CodexAppServerProcessExitedError":
        return "Codex exited before it could report usage."
    return "Codex did not answer the usage request."


def mergeCodexRateLimits(
    previous: dict[str, Any] | None, update: dict[str, Any]
) -> dict[str, Any] | None:
    if update.get("limitId") and update["limitId"] != "codex":
        return previous
    if previous is None:
        return update
    return {
        **previous,
        **{
            field: update[field]
            for field in (
                "limitId",
                "planType",
                "rateLimitReachedType",
                "primary",
                "secondary",
            )
            if field in update
        },
    }


def formatCodexUsageLimitWait(waitMs: float) -> str:
    totalMinutes = math.ceil(waitMs / 60_000)
    days = totalMinutes // (24 * 60)
    hours = (totalMinutes % (24 * 60)) // 60
    minutes = totalMinutes % 60
    if days > 0:
        return f"{days}d" if hours == 0 else f"{days}d {hours}h"
    if hours == 0:
        return f"{totalMinutes}m"
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"


def codexUsageLimitNextStep(rateLimitReachedType: str | None) -> str:
    if rateLimitReachedType in (
        "workspace_owner_credits_depleted",
        "workspace_member_credits_depleted",
    ):
        return (
            " The workspace has no credits to continue sooner: ask your workspace owner to add "
            "credits, or send the message again once the limit resets."
        )
    if rateLimitReachedType in (
        "workspace_owner_usage_limit_reached",
        "workspace_member_usage_limit_reached",
    ):
        return (
            " The workspace spend limit is reached: ask your workspace owner to raise it, "
            "or send the message again once the limit resets."
        )
    return " Send the message again once the limit resets."


def codexUsageLimitMessage(snapshot: dict[str, Any] | None, atIso: str) -> str:
    try:
        atMs = datetime.fromisoformat(atIso.replace("Z", "+00:00")).timestamp() * 1000
    except (ValueError, OverflowError):
        atMs = math.nan
    windows = codexRateLimitsToWindows(snapshot) if snapshot and math.isfinite(atMs) else []
    reset = ""
    latestResetMs = -math.inf
    for window in windows:
        if window["usedPercent"] < 100 or not window.get("resetsAt"):
            continue
        resetMs = (
            datetime.fromisoformat(window["resetsAt"].replace("Z", "+00:00")).timestamp() * 1000
        )
        if not math.isfinite(resetMs) or resetMs <= atMs or resetMs <= latestResetMs:
            continue
        latestResetMs = resetMs
        reset = (
            f" The {window['kind']} limit resets in {formatCodexUsageLimitWait(resetMs - atMs)}."
        )
    return (
        f"Codex usage limit reached.{reset}"
        f"{codexUsageLimitNextStep((snapshot or {}).get('rateLimitReachedType'))}"
    )
