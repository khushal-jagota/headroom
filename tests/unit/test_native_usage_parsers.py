"""Native consumption keeps identity, category semantics and unknown coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from planner.conversation.usage.adapters import (
    deduplicate_usage_records,
    parse_claude,
    parse_codex,
    project_usage_line,
)

STAMP = "2026-10-16T18:00:00Z"


def _line(kind: str, payload: object, stamp: str = STAMP) -> str:
    return json.dumps({"type": kind, "timestamp": stamp, "payload": payload})


def _usage(
    input_tokens: object = 100,
    output_tokens: object = 20,
    *,
    cached: object = 30,
    creation: object = 10,
    reasoning: object = 5,
    total: object = 120,
) -> dict[str, object]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": creation,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning,
        "total_tokens": total,
    }


def _meta(*, fork: bool = False) -> str:
    return _line(
        "session_meta",
        {
            "id": "thread-a",
            "session_id": "runtime-a",
            **({"forked_from_id": "parent-a"} if fork else {}),
        },
    )


def _context() -> str:
    return _line("turn_context", {"model": "gpt-6.1-sol", "turn_id": "turn-a"})


def _modern(
    response: str = "response-a",
    usage: dict[str, object] | None = None,
    *,
    cumulative: dict[str, object] | None = None,
    thread: str = "thread-a",
    session: str = "runtime-a",
    stamp: str = STAMP,
) -> str:
    actual = _usage() if usage is None else usage
    return _line(
        "token_usage_record",
        {
            "thread_id": thread,
            "session_id": session,
            "turn_id": "turn-a",
            "root_turn_id": "root-turn-a",
            "response_id": response,
            "usage": actual,
            "turn_token_usage": cumulative or actual,
            "thread_token_usage": cumulative or actual,
        },
        stamp,
    )


def _legacy(
    last: dict[str, object] | None = None,
    cumulative: dict[str, object] | None = None,
) -> str:
    actual = _usage() if last is None else last
    return _line(
        "event_msg",
        {
            "type": "token_count",
            "info": {"last_token_usage": actual, "total_token_usage": cumulative or actual},
        },
    )


def _claude(
    *,
    message: str | None = "msg-a",
    request: str | None = "req-a",
    usage: dict[str, object] | None = None,
    cost: object = None,
    session: str = "session-a",
    stamp: str = STAMP,
) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": stamp,
            "sessionId": session,
            "requestId": request,
            "costUSD": cost,
            "message": {
                "id": message,
                "model": "claude-opus-5",
                "content": "private prompt",
                "usage": usage
                if usage is not None
                else {
                    "input_tokens": 100,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 10,
                    "output_tokens": 20,
                    "output_tokens_details": {"thinking_tokens": 5},
                },
            },
        }
    )


def test_codex_native_response_wins_over_legacy_and_cumulative_representations() -> None:
    parsed = parse_codex([_meta(), _context(), _modern(), _legacy()])

    assert len(parsed.records) == 1
    record = parsed.records[0]
    assert record.format == "codex_response"
    assert record.thread_id == "thread-a"
    assert record.session_id == "runtime-a"
    assert record.tokens.input_tokens == 100
    assert record.tokens.uncached_input_tokens == 60
    assert record.tokens.cached_input_tokens == 30
    assert record.tokens.cache_creation_tokens == 10
    assert record.tokens.output_tokens == 20
    assert record.tokens.reasoning_tokens == 5
    assert record.reported_cost_usd is None
    assert parsed.modern_threads == ("thread-a",)
    assert parsed.issues == ()


def test_codex_equal_sized_native_responses_remain_distinct() -> None:
    parsed = parse_codex([_context(), _modern("response-a"), _modern("response-b")])

    assert len(parsed.records) == 2
    assert len({record.record_key for record in parsed.records}) == 2
    assert sum(record.tokens.output_tokens or 0 for record in parsed.records) == 40


def test_copied_native_response_uses_original_ids_despite_fork_container_and_timestamp() -> None:
    parsed = parse_codex(
        [
            _line("session_meta", {"id": "child-thread", "forked_from_id": "thread-a"}),
            _context(),
            _modern(),
            _modern(stamp="2026-10-16T19:00:00Z"),
        ]
    )

    assert len(parsed.records) == 1
    assert parsed.duplicates == 1
    assert parsed.records[0].thread_id == "thread-a"
    assert parsed.records[0].session_id == "runtime-a"
    assert parsed.modern_threads == ("thread-a",)


def test_same_native_response_keeps_earliest_timestamp_independent_of_copy_order() -> None:
    parsed = parse_codex(
        [
            _context(),
            _modern(stamp="2026-10-16T19:00:00Z"),
            _modern(),
        ]
    )

    assert len(parsed.records) == 1
    assert parsed.records[0].occurred_at == datetime(2026, 10, 16, 18, tzinfo=UTC)


def test_modern_fork_ledger_does_not_inherit_ignored_legacy_ambiguity() -> None:
    parsed = parse_codex([_meta(fork=True), _context(), _modern(), _legacy()])

    assert len(parsed.records) == 1
    assert parsed.records[0].format == "codex_response"
    assert parsed.issues == ()


def test_conflicting_native_identity_is_excluded_without_legacy_gap_fill() -> None:
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _modern(),
            _modern(usage=_usage(output_tokens=40, total=140)),
            _legacy(),
        ]
    )

    assert parsed.records == ()
    assert parsed.modern_threads == ("thread-a",)
    assert parsed.conflicted_record_keys == ("codex:response:response-a",)
    assert any("Conflicting" in message for message in parsed.issues)


def test_codex_unknown_turn_never_borrows_model_from_latest_other_context() -> None:
    raw = json.loads(_modern())
    raw["payload"]["turn_id"] = "unseen-turn"
    parsed = parse_codex([_context(), json.dumps(raw)])

    assert len(parsed.records) == 1
    assert parsed.records[0].model is None
    assert any("verified model" in message for message in parsed.issues)


def test_codex_known_earlier_turn_keeps_its_model_after_another_turn_context() -> None:
    parsed = parse_codex(
        [
            _context(),
            _line("turn_context", {"turn_id": "turn-b", "model": "another-model"}),
            _modern(),
        ]
    )

    assert parsed.records[0].model == "gpt-6.1-sol"
    assert parsed.issues == ()


def test_modern_native_and_container_sessions_are_not_substituted_for_each_other() -> None:
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _modern(thread="original-thread", session="original-runtime"),
        ]
    )

    record = parsed.records[0]
    assert (record.thread_id, record.session_id) == ("original-thread", "original-runtime")
    assert parsed.modern_threads == ("original-thread",)


@pytest.mark.parametrize("with_meta", [True, False])
def test_modern_response_without_original_thread_is_excluded_as_partial(with_meta: bool) -> None:
    raw = json.loads(_modern())
    del raw["payload"]["thread_id"]
    lines = [_context(), json.dumps(raw), _legacy()]
    if with_meta:
        lines.insert(0, _meta())
    parsed = parse_codex(lines)

    assert parsed.records == ()
    assert parsed.modern_threads == (("thread-a",) if with_meta else ())
    assert any("original thread" in message for message in parsed.issues)


def test_legacy_equal_sized_requests_use_cumulative_identity_and_drop_reemission() -> None:
    first = _usage()
    twice = _usage(200, 40, cached=60, creation=20, reasoning=10, total=240)
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _legacy(first, first),
            _legacy(first, first),
            _legacy(first, twice),
        ]
    )

    assert len(parsed.records) == 2
    assert all(record.format == "codex_legacy" for record in parsed.records)
    assert len({record.record_key for record in parsed.records}) == 2
    assert parsed.duplicates == 1
    assert parsed.issues == ("Legacy Codex usage has limited producer provenance.",)


def test_unknown_legacy_producer_version_cannot_claim_complete_coverage() -> None:
    parsed = parse_codex(
        [
            _line("session_meta", {"id": "thread-a", "cli_version": "unknown-historical-build"}),
            _context(),
            _legacy(),
        ]
    )

    assert len(parsed.records) == 1
    assert any("provenance" in message for message in parsed.issues)


def test_legacy_unknown_initial_baseline_is_excluded_then_verified_delta_counts() -> None:
    twice = _usage(200, 40, cached=60, creation=20, reasoning=10, total=240)
    three = _usage(300, 60, cached=90, creation=30, reasoning=15, total=360)
    parsed = parse_codex(
        [_meta(), _context(), _legacy(cumulative=twice), _legacy(cumulative=three)]
    )

    assert len(parsed.records) == 1
    assert parsed.records[0].tokens.input_tokens == 100
    assert any("baseline" in message for message in parsed.issues)


def test_legacy_counter_reset_starts_new_epoch_without_counting_reset_baseline() -> None:
    initial = _usage()
    reset = _usage(10, 2, cached=3, creation=1, reasoning=1, total=12)
    next_total = _usage(110, 22, cached=33, creation=11, reasoning=6, total=132)
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _legacy(initial, initial),
            _legacy(reset, reset),
            _legacy(initial, next_total),
        ]
    )

    assert len(parsed.records) == 2
    assert parsed.records[0].record_key != parsed.records[1].record_key
    assert all(record.tokens.input_tokens == 100 for record in parsed.records)
    assert any("reset" in message for message in parsed.issues)


def test_legacy_context_estimates_and_rate_limits_never_create_consumption() -> None:
    estimate = _usage(0, 0, cached=0, creation=0, reasoning=0, total=999)
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _legacy(),
            _legacy(estimate, _usage()),
            _line("event_msg", {"type": "token_count", "info": None, "rate_limits": {}}),
        ]
    )

    assert len(parsed.records) == 1
    assert parsed.records[0].tokens.input_tokens == 100


def test_legacy_forked_history_is_excluded_instead_of_timing_based_guess() -> None:
    parsed = parse_codex([_meta(fork=True), _context(), _legacy()])

    assert parsed.records == ()
    assert any("fork" in message for message in parsed.issues)


def test_mixed_upgrade_prefix_is_excluded_with_partial_coverage() -> None:
    twice = _usage(200, 40, cached=60, creation=20, reasoning=10, total=240)
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _legacy(),
            _modern("response-b", cumulative=twice),
            _legacy(cumulative=twice),
        ]
    )

    assert len(parsed.records) == 1
    assert parsed.records[0].format == "codex_response"
    assert any("prefix" in message for message in parsed.issues)


def test_unverified_legacy_baseline_in_mixed_history_still_marks_partial_coverage() -> None:
    twice = _usage(200, 40, cached=60, creation=20, reasoning=10, total=240)
    three = _usage(300, 60, cached=90, creation=30, reasoning=15, total=360)
    parsed = parse_codex(
        [
            _meta(),
            _context(),
            _legacy(cumulative=twice),
            _modern("response-c", cumulative=three),
            _legacy(cumulative=three),
        ]
    )

    assert len(parsed.records) == 1
    assert any("prefix" in message for message in parsed.issues)


@pytest.mark.parametrize("bad", [None, True, False, -1, 1.5, "100", float("nan"), float("inf")])
def test_invalid_codex_counter_is_unknown_without_destroying_independent_categories(
    bad: object,
) -> None:
    parsed = parse_codex([_context(), _modern(usage=_usage(input_tokens=bad))])

    tokens = parsed.records[0].tokens
    assert tokens.input_tokens is None
    assert tokens.uncached_input_tokens is None
    assert tokens.cached_input_tokens == 30
    assert tokens.cache_creation_tokens == 10
    assert tokens.output_tokens == 20


def test_valid_codex_zero_counts_remain_known_zero() -> None:
    zero = _usage(0, 0, cached=0, creation=0, reasoning=0, total=0)
    tokens = parse_codex([_context(), _modern(usage=zero)]).records[0].tokens

    assert tokens.input_tokens == tokens.uncached_input_tokens == tokens.output_tokens == 0
    assert (
        tokens.cached_input_tokens == tokens.cache_creation_tokens == tokens.reasoning_tokens == 0
    )


def test_inconsistent_codex_cache_and_reasoning_remain_unknown_without_clamping() -> None:
    parsed = parse_codex(
        [
            _context(),
            _modern(usage=_usage(cached=120, reasoning=30)),
        ]
    )

    tokens = parsed.records[0].tokens
    assert tokens.input_tokens is None
    assert tokens.uncached_input_tokens is None
    assert tokens.cached_input_tokens == 120
    assert tokens.reasoning_tokens is None
    assert len(parsed.issues) == 2


def test_legacy_missing_cache_category_preserves_known_inclusive_input() -> None:
    usage: dict[str, object] = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    parsed = parse_codex([_meta(), _context(), _legacy(usage, usage)])

    assert len(parsed.records) == 1
    assert parsed.records[0].tokens.input_tokens == 100
    assert parsed.records[0].tokens.uncached_input_tokens == 100
    assert parsed.records[0].tokens.cached_input_tokens == 0
    assert parsed.records[0].tokens.cache_creation_tokens == 0


def test_claude_normalizes_separate_input_categories_and_keeps_reasoning_subset() -> None:
    record = parse_claude([_claude(cost=0.125)]).records[0]

    assert record.tokens.input_tokens == 140
    assert record.tokens.uncached_input_tokens == 100
    assert record.tokens.cached_input_tokens == 30
    assert record.tokens.cache_creation_tokens == 10
    assert record.tokens.output_tokens == 20
    assert record.tokens.reasoning_tokens == 5
    assert record.reported_cost_usd == 0.125
    assert record.occurred_at == datetime(2026, 10, 16, 18, tzinfo=UTC)


def test_claude_unknown_cache_does_not_erase_known_uncached_input() -> None:
    parsed = parse_claude([_claude(usage={"input_tokens": 100, "output_tokens": 20})])

    tokens = parsed.records[0].tokens
    assert tokens.uncached_input_tokens == 100
    assert tokens.input_tokens is None
    assert tokens.cached_input_tokens is None
    assert tokens.cache_creation_tokens is None
    assert tokens.reasoning_tokens is None


def test_invalid_claude_categories_stay_unknown_while_explicit_subsets_stay_known() -> None:
    parsed = parse_claude(
        [
            _claude(
                usage={
                    "input_tokens": 100,
                    "cache_read_input_tokens": True,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": -1,
                    "output_tokens_details": {"thinking_tokens": 0},
                }
            )
        ]
    )

    tokens = parsed.records[0].tokens
    assert tokens.uncached_input_tokens == 100
    assert tokens.input_tokens is None
    assert tokens.cached_input_tokens is None
    assert tokens.cache_creation_tokens == 5
    assert tokens.output_tokens is None
    assert tokens.reasoning_tokens == 0


def test_claude_repeated_content_blocks_and_copied_messages_count_once() -> None:
    parsed = parse_claude([_claude(), _claude(stamp="2026-10-16T18:01:00Z")])

    assert len(parsed.records) == 1
    assert parsed.duplicates == 1
    assert parsed.issues == ()


def test_claude_copied_message_keeps_tokens_but_marks_uncertain_session_attribution() -> None:
    parsed = parse_claude([_claude(), _claude(session="fork-session")])

    assert len(parsed.records) == 1
    assert parsed.records[0].tokens.input_tokens == 140
    assert parsed.records[0].session_id is None
    assert parsed.duplicates == 1
    assert parsed.issues


def test_claude_identity_conflict_is_excluded() -> None:
    parsed = parse_claude(
        [
            _claude(),
            _claude(usage={"input_tokens": 500, "output_tokens": 20}),
            _claude(),
        ]
    )

    assert parsed.records == ()
    assert parsed.conflicted_record_keys == ('claude:message:["msg-a","req-a"]',)
    assert any("Conflicting" in message for message in parsed.issues)


@pytest.mark.parametrize(
    "message_id,request_id",
    [("msg-a", None), (None, "req-a"), ("msg-a", "req-b"), ("msg-b", "req-a")],
)
def test_claude_overlapping_identity_aliases_never_create_second_consumption(
    message_id: str | None,
    request_id: str | None,
) -> None:
    parsed = parse_claude(
        [
            _claude(),
            _claude(message=message_id, request=request_id),
        ]
    )

    expected = {
        'claude:message:["msg-a","req-a"]',
        "claude:message:" + json.dumps([message_id, request_id], separators=(",", ":")),
    }
    assert parsed.records == ()
    assert set(parsed.conflicted_record_keys) == expected
    assert any("Overlapping" in message for message in parsed.issues)


def test_claude_transitive_alias_overlap_tombstones_every_connected_identity() -> None:
    parsed = parse_claude(
        [
            _claude(),
            _claude(request="req-b"),
            _claude(message="msg-b", request="req-b"),
        ]
    )

    assert parsed.records == ()
    assert set(parsed.conflicted_record_keys) == {
        'claude:message:["msg-a","req-a"]',
        'claude:message:["msg-a","req-b"]',
        'claude:message:["msg-b","req-b"]',
    }


def test_cross_file_identity_policy_matches_parser_alias_suppression() -> None:
    first = parse_claude([_claude()])
    second = parse_claude([_claude(request=None)])
    merged = deduplicate_usage_records((*first.records, *second.records))
    single = parse_claude([_claude(), _claude(request=None)])

    assert merged.records == single.records == ()
    assert merged.conflicted_record_keys == single.conflicted_record_keys
    assert any("Overlapping" in message for message in merged.issues)


def test_persisted_alias_conflicts_suppress_new_copies_without_original_conflicting_records() -> (
    None
):
    previous = parse_claude([_claude(), _claude(request=None)])
    new_file = parse_claude([_claude(request="req-c")])
    merged = deduplicate_usage_records(
        new_file.records,
        conflicted_record_keys=previous.conflicted_record_keys,
    )

    assert merged.records == ()
    assert set(merged.conflicted_record_keys) == {
        'claude:message:["msg-a","req-a"]',
        'claude:message:["msg-a",null]',
        'claude:message:["msg-a","req-c"]',
    }
    assert merged.issues


def test_nonoverlapping_claude_message_and_request_pairs_remain_distinct_globally() -> None:
    first = parse_claude([_claude()])
    second = parse_claude([_claude(message="msg-b", request="req-b")])
    merged = deduplicate_usage_records((*first.records, *second.records))

    assert len(merged.records) == 2
    assert merged.conflicted_record_keys == ()
    assert merged.issues == ()


@pytest.mark.parametrize("message,request_id", [(None, "req-a"), ("msg-a", None)])
def test_claude_uses_a_present_identity_half(message: str | None, request_id: str | None) -> None:
    parsed = parse_claude(
        [
            _claude(message=message, request=request_id),
            _claude(message=message, request=request_id),
        ]
    )

    assert len(parsed.records) == 1
    assert parsed.duplicates == 1


@pytest.mark.parametrize("cost", [None, -1, True, "0.5", float("inf")])
def test_absent_or_invalid_claude_usd_stays_unknown(cost: object) -> None:
    assert parse_claude([_claude(cost=cost)]).records[0].reported_cost_usd is None


def test_explicit_claude_zero_cost_and_zero_categories_are_valid() -> None:
    record = parse_claude(
        [
            _claude(
                cost=0,
                usage={
                    "input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 0,
                },
            )
        ]
    ).records[0]

    assert record.tokens.input_tokens == 0
    assert record.tokens.uncached_input_tokens == 0
    assert record.reported_cost_usd == 0.0


def test_usage_without_timestamp_or_stable_identity_is_excluded() -> None:
    assert parse_claude([_claude(message=None, request=None)]).records == ()
    assert parse_claude([_claude(stamp="2026-10-16")]).records == ()
    assert parse_codex([_modern(response="")]).records == ()
    assert parse_codex([_modern(stamp="invalid")]).records == ()


def test_projection_and_raw_envelopes_never_retain_messages_tools_or_unselected_fields() -> None:
    raw = json.loads(_claude())
    raw["prompt"] = "private prompt"
    raw["message"]["usage"]["tool_text"] = "private tool"
    raw["message"]["usage"]["cache_read_input_tokens"] = "private value"
    projection = project_usage_line("claude", json.dumps(raw))

    assert projection is not None
    assert "private" not in projection
    assert "content" not in projection
    assert "prompt" not in projection
    assert project_usage_line("claude", projection) == projection
    record = parse_claude([projection]).records[0]
    assert "private" not in json.dumps(record.raw_usage)
    assert record.tokens.cached_input_tokens is None


def test_codex_projection_keeps_only_native_usage_and_context_metadata() -> None:
    raw = json.loads(_modern())
    raw["payload"]["prompt"] = "private prompt"
    raw["payload"]["usage"]["content"] = "private tool output"
    projection = project_usage_line("codex", json.dumps(raw))

    assert projection is not None
    assert "private" not in projection
    assert project_usage_line("codex", projection) == projection
    assert "private" not in json.dumps(parse_codex([projection]).records[0].raw_usage)
    assert project_usage_line("codex", _line("response_item", {"content": "private"})) is None


def test_malformed_relevant_lines_survive_projection_as_content_free_issues() -> None:
    line = '{"type":"token_usage_record", "private":'
    projection = project_usage_line("codex", line)

    assert projection == '{"type":"usage_parse_error"}'
    assert parse_codex([line]).issues == parse_codex([projection]).issues
    assert parse_claude(['{"type":"assistant","usage":']).issues
    assert project_usage_line("claude", "not a relevant JSON line") is None


@pytest.mark.parametrize("cache_write", [None, "invalid", -1])
def test_explicit_invalid_cache_write_remains_unknown(cache_write: object) -> None:
    usage = _usage()
    usage["cache_write_input_tokens"] = cache_write
    record = parse_codex([_meta(), _context(), _modern(usage=usage)]).records[0]
    assert record.tokens.cache_creation_tokens is None
    assert record.tokens.uncached_input_tokens is None


def test_omitted_codex_cache_write_matches_t3_uncached_input_and_price() -> None:
    from planner.conversation.usage.logic.pricing import parse_rate_table, price_usage

    usage = _usage()
    usage.pop("cache_write_input_tokens")
    record = parse_codex([_meta(), _context(), _modern(usage=usage)]).records[0]
    assert record.tokens.cache_creation_tokens == 0
    assert record.tokens.uncached_input_tokens == 70
    table = parse_rate_table(
        {
            "gpt-6.1-sol": {
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
                "cache_read_input_token_cost": 0.1e-6,
            }
        }
    )
    price = price_usage(table, record)
    assert price.cost_source == "model_priced"
    assert price.cost_usd == pytest.approx(70e-6 + 30 * 0.1e-6 + 20 * 2e-6)
    assert record.tokens.reasoning_tokens == 5
