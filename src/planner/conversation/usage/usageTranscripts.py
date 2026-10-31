"""Mechanical translation of T3 usageTranscripts.ts, revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
Pure line-at-a-time provider transcript reducers. Field names follow T3.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any, TypeGuard

UsageRecord = dict[str, Any]
CodexScanState = dict[str, Any]
EMPTY_TOTALS = dict(
    uncachedInputTokens=0,
    cachedInputTokens=0,
    cacheCreationTokens=0,
    outputTokens=0,
    reasoningTokens=0,
)
GROK_COST_USD_TICKS_PER_DOLLAR = 10_000_000_000
FORK_COPY_MAX_GAP_MS = 1000


def finiteNumber(value: Any) -> TypeGuard[int | float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # JSON numbers outside the JavaScript finite-number range are not usable.
        return False


def positiveInt(value: Any) -> int:
    return math.trunc(value) if finiteNumber(value) and value > 0 else 0


def parseTimestampMs(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Date.parse truncates sub-millisecond fractional digits.
        delta = parsed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    except (ValueError, OverflowError, OSError):
        return None


def addTotals(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {key: a[key] + b[key] for key in EMPTY_TOTALS}


def totalTokens(totals: dict[str, Any]) -> int:
    return sum(totals[key] for key in EMPTY_TOTALS if key != "reasoningTokens")


def mightCarryUsage(line: str, provider: str) -> bool:
    return (
        '"usage"' in line
        if provider == "claude"
        else '"turn_completed"' in line
        if provider == "grok"
        else '"token_count"' in line
    )


def grokCostTicksToUsd(ticks: Any) -> float | None:
    return ticks / GROK_COST_USD_TICKS_PER_DOLLAR if finiteNumber(ticks) and ticks >= 0 else None


def parseClaudeLine(line: str) -> UsageRecord | None:
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "assistant":
        return None
    message = record.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    usage = message["usage"]
    timestampMs = parseTimestampMs(record.get("timestamp"))
    if timestampMs is None:
        return None
    model = message.get("model")
    if not isinstance(model, str) or not model:
        return None
    messageId = message.get("id") if isinstance(message.get("id"), str) else None
    requestId = record.get("requestId") if isinstance(record.get("requestId"), str) else None
    dedupeKey = (
        None if messageId is None and requestId is None else f"{messageId or ''}:{requestId or ''}"
    )
    cost = record.get("costUSD")
    return dict(
        provider="claude",
        timestampMs=timestampMs,
        model=model,
        sessionId=record.get("sessionId") if isinstance(record.get("sessionId"), str) else "",
        totals=dict(
            uncachedInputTokens=positiveInt(usage.get("input_tokens")),
            cachedInputTokens=positiveInt(usage.get("cache_read_input_tokens")),
            cacheCreationTokens=positiveInt(usage.get("cache_creation_input_tokens")),
            outputTokens=positiveInt(usage.get("output_tokens")),
            reasoningTokens=0,
        ),
        reportedCostUsd=cost if finiteNumber(cost) else None,
        dedupeKey=dedupeKey,
    )


def initialCodexScanState() -> CodexScanState:
    return dict(
        model="",
        sessionId="",
        lastUsageSignature=None,
        sawSessionMeta=False,
        suppressingForkCopies=False,
        forkCopyAnchorMs=0,
    )


def isForkedSessionMeta(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("forked_from_id"), str):
        return True
    source = payload.get("source")
    if not isinstance(source, dict):
        return False
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return False
    spawn = subagent.get("thread_spawn")
    return isinstance(spawn, dict) and isinstance(spawn.get("parent_thread_id"), str)


def parseCodexLine(line: str, state: CodexScanState) -> UsageRecord | None:
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
        return None
    payload = record["payload"]
    if record.get("type") == "session_meta":
        if state["sawSessionMeta"]:
            return None
        state["sawSessionMeta"] = True
        identifier = (
            payload.get("id") if payload.get("id") is not None else payload.get("session_id")
        )
        if isinstance(identifier, str):
            state["sessionId"] = identifier
        timestampMs = parseTimestampMs(record.get("timestamp"))
        if timestampMs is not None and isForkedSessionMeta(payload):
            state["suppressingForkCopies"] = True
            state["forkCopyAnchorMs"] = timestampMs
        return None
    if record.get("type") == "turn_context":
        if isinstance(payload.get("model"), str):
            state["model"] = payload["model"]
        return None
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("last_token_usage"), dict):
        return None
    last = info["last_token_usage"]
    timestampMs = parseTimestampMs(record.get("timestamp"))
    if timestampMs is None or not state["model"]:
        return None
    signature = json.dumps(last, separators=(",", ":"), ensure_ascii=False)
    if signature == state["lastUsageSignature"]:
        return None
    state["lastUsageSignature"] = signature
    if state["suppressingForkCopies"]:
        if timestampMs - state["forkCopyAnchorMs"] < FORK_COPY_MAX_GAP_MS:
            state["forkCopyAnchorMs"] = timestampMs
            return None
        state["suppressingForkCopies"] = False
    inputTokens = positiveInt(last.get("input_tokens"))
    cachedInputTokens = positiveInt(last.get("cached_input_tokens"))
    cacheCreationTokens = positiveInt(last.get("cache_write_input_tokens"))
    outputTokens = positiveInt(last.get("output_tokens"))
    totals = dict(
        uncachedInputTokens=max(0, inputTokens - cachedInputTokens - cacheCreationTokens),
        cachedInputTokens=cachedInputTokens,
        cacheCreationTokens=cacheCreationTokens,
        outputTokens=outputTokens,
        reasoningTokens=min(outputTokens, positiveInt(last.get("reasoning_output_tokens"))),
    )
    if totalTokens(totals) == 0:
        return None
    return dict(
        provider="codex",
        timestampMs=timestampMs,
        model=state["model"],
        sessionId=state["sessionId"],
        totals=totals,
        reportedCostUsd=None,
        dedupeKey=None,
    )


def readGrokUsageTotals(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        **{
            key: positiveInt(value.get(key))
            for key in (
                "inputTokens",
                "outputTokens",
                "cachedReadTokens",
                "cacheCreationTokens",
                "reasoningTokens",
            )
        },
        "costUsdTicks": value.get("costUsdTicks")
        if finiteNumber(value.get("costUsdTicks"))
        else None,
    }


def grokTotalsToUsage(totals: dict[str, Any]) -> dict[str, Any]:
    return dict(
        uncachedInputTokens=max(
            0, totals["inputTokens"] - totals["cachedReadTokens"] - totals["cacheCreationTokens"]
        ),
        cachedInputTokens=totals["cachedReadTokens"],
        cacheCreationTokens=totals["cacheCreationTokens"],
        outputTokens=totals["outputTokens"],
        reasoningTokens=min(totals["outputTokens"], totals["reasoningTokens"]),
    )


def parseGrokLine(line: str) -> list[UsageRecord]:
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return []
    if not isinstance(record, dict) or not isinstance(record.get("params"), dict):
        return []
    params = record["params"]
    update = params.get("update")
    if not isinstance(update, dict) or update.get("sessionUpdate") != "turn_completed":
        return []
    usage = update.get("usage")
    if not isinstance(usage, dict):
        return []
    sessionId = params.get("sessionId") if isinstance(params.get("sessionId"), str) else ""
    promptId = update.get("prompt_id") if isinstance(update.get("prompt_id"), str) else None
    meta = params.get("_meta")
    timestampMs = meta.get("agentTimestampMs") if isinstance(meta, dict) else None
    if not finiteNumber(timestampMs):
        timestamp = record.get("timestamp")
        if not finiteNumber(timestamp):
            return []
        timestampMs = timestamp if timestamp > 1e12 else timestamp * 1000
    topLevel = readGrokUsageTotals(usage)
    if topLevel is None:
        return []
    modelUsage = usage.get("modelUsage")
    modelEntries = []
    if isinstance(modelUsage, dict):
        for model, raw in modelUsage.items():
            if not model:
                continue
            totals = readGrokUsageTotals(raw)
            if totals is not None:
                modelEntries.append((model, totals))
    if not modelEntries:
        totals = grokTotalsToUsage(topLevel)
        if totalTokens(totals) == 0:
            return []
        return [
            dict(
                provider="grok",
                timestampMs=timestampMs,
                model="grok",
                sessionId=sessionId,
                totals=totals,
                reportedCostUsd=grokCostTicksToUsd(topLevel["costUsdTicks"]),
                dedupeKey=None if promptId is None else f"{sessionId}:{promptId}:grok",
            )
        ]
    topLevelCostUsd = grokCostTicksToUsd(topLevel["costUsdTicks"])
    usedTickedCostUsd = 0.0
    untickedTokenDenominator = 0
    for _model, totals in modelEntries:
        tokens = totalTokens(grokTotalsToUsage(totals))
        if tokens == 0:
            continue
        if totals["costUsdTicks"] is not None:
            usedTickedCostUsd += grokCostTicksToUsd(totals["costUsdTicks"]) or 0
        else:
            untickedTokenDenominator += tokens
    remainingCostUsd = (
        None if topLevelCostUsd is None else max(0, topLevelCostUsd - usedTickedCostUsd)
    )
    results = []
    for model, rawTotals in modelEntries:
        totals = grokTotalsToUsage(rawTotals)
        if totalTokens(totals) == 0:
            continue
        reportedCostUsd = grokCostTicksToUsd(rawTotals["costUsdTicks"])
        if (
            reportedCostUsd is None
            and remainingCostUsd is not None
            and untickedTokenDenominator > 0
        ):
            reportedCostUsd = remainingCostUsd * (totalTokens(totals) / untickedTokenDenominator)
        results.append(
            dict(
                provider="grok",
                timestampMs=timestampMs,
                model=model,
                sessionId=sessionId,
                totals=totals,
                reportedCostUsd=reportedCostUsd,
                dedupeKey=None if promptId is None else f"{sessionId}:{promptId}:{model}",
            )
        )
    return results
