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
        else any(
            marker in line
            for marker in (
                '"token_count"',
                '"token_usage_record"',
                '"compacted"',
                '"thread_settings_applied"',
            )
        )
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
    cacheCreation = usage.get("cache_creation")
    cacheCreation1hTokens = (
        min(
            positiveInt(usage.get("cache_creation_input_tokens")),
            positiveInt(cacheCreation.get("ephemeral_1h_input_tokens")),
        )
        if isinstance(cacheCreation, dict)
        else 0
    )
    return dict(
        **({"cacheCreation1hTokens": cacheCreation1hTokens} if cacheCreation1hTokens else {}),
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
        serviceTier=None,
        compactions={},
        compactionMarkers={},
        completedCompactions=[],
        cumulativeUsage=None,
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


_COUNTERS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _usageCounters(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {key: positiveInt(value[key]) for key in _COUNTERS if key in value}


def _compactionRecord(state: CodexScanState, responseId: str) -> UsageRecord | None:
    candidate = state["compactions"].get(responseId)
    if candidate is None or responseId not in state["compactionMarkers"]:
        return None
    del state["compactions"][responseId]
    del state["compactionMarkers"][responseId]
    state["completedCompactions"].append(responseId)
    record: UsageRecord = candidate["record"]
    # Keep an identity with no additional consumption when token_count already
    # covers this request. Cross-file deduplication runs before date filtering.
    if candidate["covered"]:
        record["totals"] = dict(EMPTY_TOTALS)
        record["compactionCovered"] = True
    return record


def _snapshotCovers(candidate: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    current, previous, last = snapshot["current"], snapshot["previous"], snapshot["last"]
    usage, cumulative = candidate["usage"], candidate["cumulative"]
    if not current or current == previous:
        return False
    if cumulative:
        return all(current.get(k) == v for k, v in cumulative.items())
    if previous is not None and usage and last == usage:
        keys = ("input_tokens", "output_tokens")
        return all(k in current and k in previous and k in usage for k in keys) and all(
            current[k] - previous[k] == usage[k] for k in keys
        )
    return False


def _observeCompactionOverlap(state: CodexScanState, info: dict[str, Any]) -> None:
    current = _usageCounters(info.get("total_token_usage"))
    snapshot = dict(
        current=current,
        previous=state["cumulativeUsage"],
        last=_usageCounters(info.get("last_token_usage")),
    )
    # Only accepted normal records reach this point. Preserve snapshots after a
    # marker too, because the matching response can arrive later in the file.
    for snapshots in state["compactionMarkers"].values():
        snapshots.append(snapshot)
    for candidate in state["compactions"].values():
        candidate["covered"] = candidate["covered"] or _snapshotCovers(candidate, snapshot)
    if current:
        state["cumulativeUsage"] = current


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
    if payload.get("type") == "thread_settings_applied":
        settings = payload.get("thread_settings")
        if isinstance(settings, dict) and "service_tier" in settings:
            tier = settings["service_tier"]
            state["serviceTier"] = tier if isinstance(tier, str) else None
        return None
    if record.get("type") == "compacted":
        responseId = payload.get("compaction_response_id")
        if (
            isinstance(responseId, str)
            and responseId
            and responseId not in state["completedCompactions"]
        ):
            if responseId not in state["compactionMarkers"]:
                state["compactionMarkers"][responseId] = []
            return _compactionRecord(state, responseId)
        return None
    if record.get("type") == "token_usage_record":
        responseId = payload.get("response_id")
        usage = _usageCounters(payload.get("usage"))
        timestampMs = parseTimestampMs(record.get("timestamp"))
        if (
            not isinstance(responseId, str)
            or not responseId
            or not usage
            or timestampMs is None
            or not state["model"]
            or responseId in state["completedCompactions"]
            or responseId in state["compactions"]
        ):
            return None
        inclusive = usage.get("input_tokens", 0)
        cached = usage.get("cached_input_tokens", 0)
        creation = usage.get("cache_write_input_tokens", 0)
        output = usage.get("output_tokens", 0)
        state["compactions"][responseId] = dict(
            usage=usage,
            cumulative=_usageCounters(payload.get("thread_token_usage")),
            covered=False,
            record=dict(
                provider="codex",
                timestampMs=timestampMs,
                model=payload.get("model")
                if isinstance(payload.get("model"), str)
                else state["model"],
                sessionId=payload.get("session_id")
                if isinstance(payload.get("session_id"), str)
                else state["sessionId"],
                totals=dict(
                    uncachedInputTokens=max(0, inclusive - cached - creation),
                    cachedInputTokens=cached,
                    cacheCreationTokens=creation,
                    outputTokens=output,
                    reasoningTokens=min(output, usage.get("reasoning_output_tokens", 0)),
                ),
                reportedCostUsd=None,
                dedupeKey="codex:compaction:" + responseId,
                serviceTier=payload.get("service_tier")
                if isinstance(payload.get("service_tier"), str)
                else state["serviceTier"],
            ),
        )
        candidate = state["compactions"][responseId]
        candidate["covered"] = any(
            _snapshotCovers(candidate, snapshot)
            for snapshot in state["compactionMarkers"].get(responseId, [])
        )
        return _compactionRecord(state, responseId)
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    if not isinstance(info.get("last_token_usage"), dict):
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
    _observeCompactionOverlap(state, info)
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
