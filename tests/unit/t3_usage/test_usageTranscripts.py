"""All T3 usageTranscripts.test.ts cases, revision 75e4ceb9 (MIT)."""

import json
from typing import Any, cast

import pytest

from planner.conversation.usage.usageTranscripts import (
    GROK_COST_USD_TICKS_PER_DOLLAR,
    initialCodexScanState,
    parseClaudeLine,
    parseCodexLine,
    parseGrokLine,
    totalTokens,
)


def claudeLine(
    messageId: str, contentType: str, model: str = "claude-fable-5", outputTokens: int = 286
) -> str:
    return json.dumps(
        dict(
            type="assistant",
            timestamp="2026-08-07T04:05:13.944Z",
            sessionId="5a128faa-8253-489e-b935-6c08e8e670c0",
            cwd="/home/theo/project",
            message=dict(
                id=messageId,
                role="assistant",
                model=model,
                content=[dict(type=contentType)],
                usage=dict(
                    input_tokens=2,
                    cache_creation_input_tokens=66818,
                    cache_read_input_tokens=1000,
                    output_tokens=outputTokens,
                ),
            ),
        )
    )


SESSION_META = json.dumps(
    dict(
        type="session_meta",
        timestamp="2026-08-01T05:17:41.289Z",
        payload=dict(type="session_meta", id="019fbbc1-b12c-7360-a685-28c181f0025f"),
    )
)
TURN_CONTEXT = json.dumps(
    dict(
        type="turn_context",
        timestamp="2026-08-01T05:17:42.694Z",
        payload=dict(type="turn_context", model="gpt-5.6-sol"),
    )
)


def tokenCount(inputTokens: int, cached: int, output: int, reasoning: int) -> str:
    return json.dumps(
        dict(
            type="event_msg",
            timestamp="2026-08-01T05:17:49.919Z",
            payload=dict(
                type="token_count",
                info=dict(
                    last_token_usage=dict(
                        input_tokens=inputTokens,
                        cached_input_tokens=cached,
                        cache_write_input_tokens=0,
                        output_tokens=output,
                        reasoning_output_tokens=reasoning,
                    )
                ),
            ),
        )
    )


def meta(
    id: str, timestamp: str, forkedFromId: str | None = None, spawnParentId: str | None = None
) -> str:
    return json.dumps(
        dict(
            type="session_meta",
            timestamp=timestamp,
            payload=dict(type="session_meta", id=id)
            | ({} if forkedFromId is None else dict(forked_from_id=forkedFromId))
            | (
                {}
                if spawnParentId is None
                else dict(
                    source=dict(subagent=dict(thread_spawn=dict(parent_thread_id=spawnParentId)))
                )
            ),
        )
    )


def stamped(timestamp: str, line: str) -> str:
    return json.dumps(json.loads(line) | dict(timestamp=timestamp))


def test_extracts_token_totals_and_a_dedupe_key() -> None:
    """extracts token totals and a dedupe key"""
    record = parseClaudeLine(claudeLine("msg_1", "text"))
    assert record is not None
    assert record["provider"] == "claude"
    assert record["model"] == "claude-fable-5"
    assert record["totals"] == dict(
        uncachedInputTokens=2,
        cachedInputTokens=1000,
        cacheCreationTokens=66818,
        outputTokens=286,
        reasoningTokens=0,
    )
    assert record["dedupeKey"] == "msg_1:"


def test_gives_every_content_block_of_one_message_the_same_dedupe_key() -> None:
    """gives every content block of one message the same dedupe key"""
    text = cast(dict[str, Any], parseClaudeLine(claudeLine("msg_2", "text")))
    toolUse = cast(dict[str, Any], parseClaudeLine(claudeLine("msg_2", "tool_use")))
    assert text["dedupeKey"] == toolUse["dedupeKey"]
    assert text["totals"] == toolUse["totals"]


def test_ignores_records_that_are_not_assistant_messages() -> None:
    """ignores records that are not assistant messages"""
    assert parseClaudeLine(json.dumps(dict(type="user", message={}))) is None
    assert parseClaudeLine("not json") is None


def test_attributes_usage_to_the_model_from_the_preceding_turn_context() -> None:
    """attributes usage to the model from the preceding turn context"""
    state = initialCodexScanState()
    parseCodexLine(SESSION_META, state)
    parseCodexLine(TURN_CONTEXT, state)
    record = cast(dict[str, Any], parseCodexLine(tokenCount(19239, 11008, 299, 116), state))
    assert record["provider"] == "codex"
    assert record["model"] == "gpt-5.6-sol"
    assert record["sessionId"] == "019fbbc1-b12c-7360-a685-28c181f0025f"
    assert record["totals"]["uncachedInputTokens"] == 19239 - 11008
    assert record["totals"]["cachedInputTokens"] == 11008
    assert record["totals"]["reasoningTokens"] == 116


def test_skips_a_repeated_token_count_so_deltas_are_not_double_counted() -> None:
    """skips a repeated token_count so deltas are not double counted"""
    state = initialCodexScanState()
    parseCodexLine(TURN_CONTEXT, state)
    assert parseCodexLine(tokenCount(100, 0, 10, 0), state) is not None
    assert parseCodexLine(tokenCount(100, 0, 10, 0), state) is None


def test_drops_usage_that_arrives_before_any_model_is_known() -> None:
    """drops usage that arrives before any model is known"""
    assert parseCodexLine(tokenCount(100, 0, 10, 0), initialCodexScanState()) is None


def test_does_not_let_a_pre_model_event_poison_the_duplicate_signature() -> None:
    """does not let a pre-model event poison the duplicate signature"""
    state = initialCodexScanState()
    assert parseCodexLine(tokenCount(100, 0, 10, 0), state) is None
    parseCodexLine(TURN_CONTEXT, state)
    assert parseCodexLine(tokenCount(100, 0, 10, 0), state) is not None


def test_keeps_the_child_session_id_over_copied_ancestor_metas() -> None:
    """keeps the child session id over copied ancestor metas"""
    state = initialCodexScanState()
    parseCodexLine(meta("child", "2026-08-01T05:00:00.000Z"), state)
    parseCodexLine(meta("parent", "2026-08-01T05:00:00.000Z"), state)
    parseCodexLine(TURN_CONTEXT, state)
    assert (
        cast(dict[str, Any], parseCodexLine(tokenCount(100, 0, 10, 0), state))["sessionId"]
        == "child"
    )


def test_drops_the_re_stamped_copied_burst_and_keeps_the_first_real_event() -> None:
    """drops the re-stamped copied burst and keeps the first real event"""
    state = initialCodexScanState()
    forkInstant = "2026-08-01T05:00:00.000Z"
    parseCodexLine(meta("child", forkInstant, forkedFromId="parent"), state)
    parseCodexLine(meta("parent", forkInstant), state)
    parseCodexLine(stamped(forkInstant, TURN_CONTEXT), state)
    assert (
        parseCodexLine(stamped("2026-08-01T05:00:00.001Z", tokenCount(100, 0, 10, 0)), state)
        is None
    )
    assert (
        parseCodexLine(stamped("2026-08-01T05:00:00.002Z", tokenCount(200, 0, 20, 0)), state)
        is None
    )
    real = parseCodexLine(stamped("2026-08-01T05:00:06.000Z", tokenCount(300, 0, 30, 0)), state)
    assert real is not None
    assert real["totals"]["outputTokens"] == 30
    assert (
        parseCodexLine(stamped("2026-08-01T05:00:06.100Z", tokenCount(400, 0, 40, 0)), state)
        is not None
    )


def test_recognizes_subagent_spawns_without_forked_from_id() -> None:
    """recognizes subagent spawns without forked_from_id"""
    state = initialCodexScanState()
    spawnInstant = "2026-08-01T05:00:00.000Z"
    parseCodexLine(meta("child", spawnInstant, spawnParentId="parent"), state)
    parseCodexLine(stamped(spawnInstant, TURN_CONTEXT), state)
    assert (
        parseCodexLine(stamped("2026-08-01T05:00:00.001Z", tokenCount(100, 0, 10, 0)), state)
        is None
    )


def test_does_not_suppress_anything_in_a_rollout_that_is_not_a_fork() -> None:
    """does not suppress anything in a rollout that is not a fork"""
    state = initialCodexScanState()
    parseCodexLine(meta("root", "2026-08-01T05:00:00.000Z"), state)
    parseCodexLine(stamped("2026-08-01T05:00:00.100Z", TURN_CONTEXT), state)
    assert (
        parseCodexLine(stamped("2026-08-01T05:00:00.200Z", tokenCount(100, 0, 10, 0)), state)
        is not None
    )


def test_does_not_add_reasoning_on_top_of_output() -> None:
    """does not add reasoning on top of output"""
    assert (
        totalTokens(
            dict(
                uncachedInputTokens=10,
                cachedInputTokens=20,
                cacheCreationTokens=30,
                outputTokens=40,
                reasoningTokens=25,
            )
        )
        == 100
    )


MISSING = object()


def turnCompleted(
    *,
    sessionId: str = "019fec1a-12f7-72f2-9b1f-7778a00aea3c",
    promptId: str = "prompt-1",
    timestamp: int = 1786372566,
    agentTimestampMs: int = 1786372566485,
    usage: dict[str, Any] | None = None,
    modelUsage: Any = MISSING,
) -> str:
    if modelUsage is MISSING:
        modelUsage = {
            "grok-4.5-build": dict(
                inputTokens=20272,
                outputTokens=272,
                totalTokens=20544,
                cachedReadTokens=11264,
                cacheCreationTokens=0,
                reasoningTokens=180,
                costUsdTicks=230272000,
            )
        }
    return json.dumps(
        dict(
            timestamp=timestamp,
            method="_x.ai/session/update",
            params=dict(
                sessionId=sessionId,
                update=dict(
                    sessionUpdate="turn_completed",
                    prompt_id=promptId,
                    stop_reason="end_turn",
                    usage=dict(
                        inputTokens=20272,
                        outputTokens=272,
                        totalTokens=20544,
                        cachedReadTokens=11264,
                        cacheCreationTokens=0,
                        reasoningTokens=180,
                        costUsdTicks=230272000,
                    )
                    | ({} if modelUsage is None else dict(modelUsage=modelUsage))
                    | (usage or {}),
                ),
                _meta=dict(eventId="event-1", agentTimestampMs=agentTimestampMs),
            ),
        )
    )


def test_extracts_per_model_totals_and_provider_reported_cost_ticks() -> None:
    """extracts per-model totals and provider-reported cost ticks"""
    records = parseGrokLine(turnCompleted())
    assert len(records) == 1
    record = records[0]
    assert record["provider"] == "grok"
    assert record["model"] == "grok-4.5-build"
    assert record["sessionId"] == "019fec1a-12f7-72f2-9b1f-7778a00aea3c"
    assert record["timestampMs"] == 1786372566485
    assert record["totals"] == dict(
        uncachedInputTokens=20272 - 11264,
        cachedInputTokens=11264,
        cacheCreationTokens=0,
        outputTokens=272,
        reasoningTokens=180,
    )
    assert record["reportedCostUsd"] == pytest.approx(
        230272000 / GROK_COST_USD_TICKS_PER_DOLLAR, abs=0.5e-12, rel=0
    )
    assert record["dedupeKey"] == "019fec1a-12f7-72f2-9b1f-7778a00aea3c:prompt-1:grok-4.5-build"


def test_emits_one_record_per_model_when_modelUsage_has_several_entries() -> None:
    """emits one record per model when modelUsage has several entries"""
    records = parseGrokLine(
        turnCompleted(
            modelUsage={
                "grok-4.5": dict(
                    inputTokens=1000,
                    outputTokens=50,
                    cachedReadTokens=400,
                    reasoningTokens=20,
                    costUsdTicks=50000000,
                ),
                "grok-composer-2.5-fast": dict(
                    inputTokens=200,
                    outputTokens=30,
                    cachedReadTokens=100,
                    reasoningTokens=0,
                    costUsdTicks=10000000,
                ),
            }
        )
    )
    assert sorted(record["model"] for record in records) == ["grok-4.5", "grok-composer-2.5-fast"]
    assert all(record["provider"] == "grok" for record in records)
    assert next(record for record in records if record["model"] == "grok-4.5")[
        "reportedCostUsd"
    ] == pytest.approx(0.005, abs=0.5e-12, rel=0)


def test_inherits_top_level_cost_ticks_for_a_single_model_without_its_own_ticks() -> None:
    """inherits top-level cost ticks for a single model without its own ticks"""
    records = parseGrokLine(
        turnCompleted(
            modelUsage={
                "grok-4.5-build": dict(
                    inputTokens=1000, outputTokens=10, cachedReadTokens=0, reasoningTokens=0
                )
            },
            usage=dict(costUsdTicks=GROK_COST_USD_TICKS_PER_DOLLAR),
        )
    )
    assert len(records) == 1
    assert records[0]["reportedCostUsd"] == 1


def test_falls_back_to_a_generic_grok_model_when_modelUsage_is_absent() -> None:
    """falls back to a generic grok model when modelUsage is absent"""
    records = parseGrokLine(turnCompleted(modelUsage=None))
    assert len(records) == 1
    record = records[0]
    assert record["provider"] == "grok"
    assert record["model"] == "grok"
    assert record["totals"] == dict(
        uncachedInputTokens=20272 - 11264,
        cachedInputTokens=11264,
        cacheCreationTokens=0,
        outputTokens=272,
        reasoningTokens=180,
    )
    assert record["reportedCostUsd"] == pytest.approx(
        230272000 / GROK_COST_USD_TICKS_PER_DOLLAR, abs=0.5e-12, rel=0
    )
    assert record["dedupeKey"] == "019fec1a-12f7-72f2-9b1f-7778a00aea3c:prompt-1:grok"


def test_pro_rates_top_level_cost_ticks_across_multi_model_turns_without_per_model_ticks() -> None:
    """pro-rates top-level cost ticks across multi-model turns without per-model ticks"""
    records = parseGrokLine(
        turnCompleted(
            modelUsage={
                "grok-4.5": dict(
                    inputTokens=300, outputTokens=0, cachedReadTokens=0, reasoningTokens=0
                ),
                "grok-composer-2.5-fast": dict(
                    inputTokens=100, outputTokens=0, cachedReadTokens=0, reasoningTokens=0
                ),
            },
            usage=dict(costUsdTicks=GROK_COST_USD_TICKS_PER_DOLLAR),
        )
    )
    assert len(records) == 2
    byModel = {record["model"]: record for record in records}
    assert byModel["grok-4.5"]["reportedCostUsd"] == pytest.approx(0.75, abs=0.5e-12, rel=0)
    assert byModel["grok-composer-2.5-fast"]["reportedCostUsd"] == pytest.approx(
        0.25, abs=0.5e-12, rel=0
    )
    assert sum(record["reportedCostUsd"] for record in records) == pytest.approx(
        1, abs=0.5e-12, rel=0
    )


def test_pro_rates_aggregate_cost_when_a_zero_token_sibling_carries_costUsdTicks_0() -> None:
    """pro-rates aggregate cost when a zero-token sibling carries costUsdTicks: 0"""
    records = parseGrokLine(
        turnCompleted(
            modelUsage={
                "grok-4.5": dict(
                    inputTokens=300, outputTokens=0, cachedReadTokens=0, reasoningTokens=0
                ),
                "grok-composer-2.5-fast": dict(
                    inputTokens=100, outputTokens=0, cachedReadTokens=0, reasoningTokens=0
                ),
                "empty-sibling": dict(
                    inputTokens=0,
                    outputTokens=0,
                    cachedReadTokens=0,
                    reasoningTokens=0,
                    costUsdTicks=0,
                ),
            },
            usage=dict(costUsdTicks=GROK_COST_USD_TICKS_PER_DOLLAR),
        )
    )
    assert len(records) == 2
    assert all(record["model"] != "empty-sibling" for record in records)
    byModel = {record["model"]: record for record in records}
    assert byModel["grok-4.5"]["reportedCostUsd"] == pytest.approx(0.75, abs=0.5e-12, rel=0)
    assert byModel["grok-composer-2.5-fast"]["reportedCostUsd"] == pytest.approx(
        0.25, abs=0.5e-12, rel=0
    )
    assert sum(record["reportedCostUsd"] for record in records) == pytest.approx(
        1, abs=0.5e-12, rel=0
    )


def test_allocates_leftover_aggregate_ticks_to_models_that_omit_per_model_ticks() -> None:
    """allocates leftover aggregate ticks to models that omit per-model ticks"""
    records = parseGrokLine(
        turnCompleted(
            modelUsage={
                "grok-4.5": dict(
                    inputTokens=300,
                    outputTokens=0,
                    cachedReadTokens=0,
                    reasoningTokens=0,
                    costUsdTicks=0.4 * GROK_COST_USD_TICKS_PER_DOLLAR,
                ),
                "grok-composer-2.5-fast": dict(
                    inputTokens=100, outputTokens=0, cachedReadTokens=0, reasoningTokens=0
                ),
            },
            usage=dict(costUsdTicks=GROK_COST_USD_TICKS_PER_DOLLAR),
        )
    )
    assert len(records) == 2
    byModel = {record["model"]: record for record in records}
    assert byModel["grok-4.5"]["reportedCostUsd"] == pytest.approx(0.4, abs=0.5e-12, rel=0)
    assert byModel["grok-composer-2.5-fast"]["reportedCostUsd"] == pytest.approx(
        0.6, abs=0.5e-12, rel=0
    )
    assert sum(record["reportedCostUsd"] for record in records) == pytest.approx(
        1, abs=0.5e-12, rel=0
    )


def test_does_not_invent_a_colliding_dedupe_key_when_prompt_id_is_missing() -> None:
    """does not invent a colliding dedupe key when prompt_id is missing"""
    line = json.dumps(
        dict(
            timestamp=1786372566,
            method="_x.ai/session/update",
            params=dict(
                sessionId="s1",
                update=dict(
                    sessionUpdate="turn_completed",
                    usage=dict(
                        inputTokens=10,
                        outputTokens=2,
                        modelUsage={"grok-4.5": dict(inputTokens=10, outputTokens=2)},
                    ),
                ),
            ),
        )
    )
    assert parseGrokLine(line)[0]["dedupeKey"] is None


def test_ignores_non_turn_lines_and_empty_usage() -> None:
    """ignores non-turn lines and empty usage"""
    assert parseGrokLine(json.dumps(dict(method="session/update", params={}))) == []
    assert parseGrokLine("not json") == []
    assert (
        parseGrokLine(
            turnCompleted(
                modelUsage={
                    "grok-4.5-build": dict(
                        inputTokens=0,
                        outputTokens=0,
                        cachedReadTokens=0,
                        reasoningTokens=0,
                        costUsdTicks=0,
                    )
                }
            )
        )
        == []
    )


def test_falls_back_to_the_outer_unix_seconds_timestamp_when_agent_meta_is_missing() -> None:
    """falls back to the outer unix-seconds timestamp when agent meta is missing"""
    line = json.dumps(
        dict(
            timestamp=1786372566,
            method="_x.ai/session/update",
            params=dict(
                sessionId="s1",
                update=dict(
                    sessionUpdate="turn_completed",
                    prompt_id="p1",
                    usage=dict(
                        inputTokens=10,
                        outputTokens=2,
                        modelUsage={"grok-4.5": dict(inputTokens=10, outputTokens=2)},
                    ),
                ),
            ),
        )
    )
    assert parseGrokLine(line)[0]["timestampMs"] == 1786372566000
