"""Stored usage has one source, exact identities, and honest calendar/unknown boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.conversation.usage import service as usage_service
from planner.conversation.usage.adapters import project_usage_line
from planner.conversation.usage.contracts import (
    ScanLimits,
    SourceSnapshot,
    UsageProvider,
    UsageRecord,
    UsageSource,
    UsageTokens,
)
from planner.conversation.usage.logic.summary import metrics, summarize
from planner.conversation.usage.service import RecordedUsageService, configured_sources
from planner.core.db import connect

NOW = datetime(2026, 10, 16, 18, tzinfo=UTC)


def record(key: str = "response", *, at: datetime = NOW - timedelta(hours=1)) -> UsageRecord:
    return UsageRecord(
        provider="codex", record_key=key, format="codex_response", occurred_at=at,
        model="model", thread_id="thread", session_id="session",
        tokens=UsageTokens(100, 20, 10, 80, 0, 2), reported_cost_usd=None, raw_usage={},
    )


def source(*records: UsageRecord, modern: tuple[str, ...] = ()) -> SourceSnapshot:
    return SourceSnapshot(
        provider="codex", source_key="source", state="ready", observed_at=NOW,
        records=records, modern_threads=modern,
    )


def native_line(key: str, *, count: int = 100, model: str = "model") -> str:
    return "\n".join(json.dumps(value) for value in (
        {"type": "session_meta", "payload": {"id": "thread"}},
        {"type": "turn_context", "payload": {"turn_id": "turn", "model": model}},
        {"type": "token_usage_record", "timestamp": "2026-10-16T17:00:00Z", "payload": {
            "thread_id": "thread", "turn_id": "turn", "response_id": key,
            "usage": {"input_tokens": count, "cached_input_tokens": 20,
                      "cache_write_input_tokens": 0, "output_tokens": 5,
                      "reasoning_output_tokens": 0, "total_tokens": count + 5},
        }},
    )) + "\n"


def test_empty_and_unknown_categories_do_not_become_zero() -> None:
    assert metrics(())["total_tokens"] is None
    assert metrics(())["reported_cost_usd"] is None
    known = record()
    partial = replace(known, record_key="partial", tokens=UsageTokens(None, 7, 5, None, None, None))
    summary = metrics((known, partial))
    assert summary["total_tokens"] is None
    assert summary["uncached_input_tokens"] == 27
    assert summary["output_tokens"] == 15
    assert summary["cached_input_tokens"] is None
    assert summary["unknown_records"] == 1
    assert summary["unknown_cost_records"] == 2
    assert metrics((replace(known, tokens=replace(known.tokens, reasoning_tokens=None)),))[
        "unknown_records"
    ] == 0


@pytest.mark.parametrize("amount", [-1.0, float("inf"), float("nan")])
def test_invalid_cost_never_becomes_spend(amount: float) -> None:
    summary = metrics((replace(record(), reported_cost_usd=amount),))
    assert summary["reported_cost_usd"] is None
    assert summary["unknown_cost_records"] == 1


def test_native_reported_zero_cost_remains_zero() -> None:
    assert metrics((replace(record(), reported_cost_usd=0.0),))["reported_cost_usd"] == 0.0


def test_global_identity_dedup_and_modern_precedence_cross_files() -> None:
    modern = record()
    copy = replace(modern, occurred_at=modern.occurred_at + timedelta(seconds=3))
    legacy = replace(modern, record_key="legacy", format="codex_legacy")
    summary = summarize(
        (source(modern, modern=("thread",)), source(copy, legacy)),
        now=NOW, days=7, timezone="UTC",
    )
    assert summary["totals"]["record_count"] == 1
    assert summary["totals"]["total_tokens"] == 110
    assert summary["sources"][0]["duplicates"] == 1


def test_conflicting_identity_is_excluded_and_coverage_is_partial() -> None:
    first = record()
    conflicting = replace(first, tokens=replace(first.tokens, output_tokens=900))
    summary = summarize((source(first, conflicting),), now=NOW, days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 0
    assert summary["totals"]["total_tokens"] is None
    assert summary["sources"][0]["state"] == "partial"
    assert summary["periods"] == []


def test_persisted_conflict_suppresses_an_earlier_verified_record() -> None:
    snapshot = replace(source(record()), conflicted_record_keys=("response",))
    summary = summarize((snapshot,), now=NOW, days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 0
    assert summary["sources"][0]["state"] == "partial"


def test_claude_copies_count_once_with_unknown_session_attribution() -> None:
    first = replace(record(), provider="claude", format="claude_message")
    second = replace(first, session_id="copy")
    snapshot = replace(source(first, second), provider="claude")
    summary = summarize((snapshot,), now=NOW, days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 1
    assert summary["totals"]["session_count"] is None
    assert summary["sources"][1]["state"] == "partial"


def test_claude_alias_conflicts_are_reconciled_across_stored_files() -> None:
    first_key = 'claude:message:["message","request"]'
    first = replace(
        record(first_key), provider="claude", format="claude_message", thread_id=None,
        raw_usage={"message_id": "message", "request_id": "request"},
    )
    partial = replace(
        first, record_key='claude:message:["message",null]',
        raw_usage={"message_id": "message", "request_id": None},
    )
    snapshots = (
        replace(source(first), provider="claude"),
        replace(source(partial), provider="claude"),
    )
    summary = summarize(snapshots, now=NOW, days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 0
    assert summary["sources"][1]["state"] == "partial"
    # The original conflicted records can disappear from parsed output. Their keys
    # still suppress a later partial copy with the same native alias.
    persisted = replace(
        source(), provider="claude", conflicted_record_keys=(first_key,),
    )
    summary = summarize((persisted, snapshots[1]), now=NOW, days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 0


def test_local_calendar_keeps_gaps_and_uses_23_hour_spring_day() -> None:
    now = datetime(2026, 3, 30, 12, tzinfo=UTC)
    sample = record(at=datetime(2026, 3, 29, 12, tzinfo=UTC))
    summary = summarize((source(sample),), now=now, days=7, timezone="Europe/Berlin")
    assert len(summary["periods"]) == 1
    assert summary["periods"][0]["start"] == "2026-03-28T23:00:00Z"
    assert summary["periods"][0]["end"] == "2026-03-29T22:00:00Z"


def test_repeated_dst_hours_have_distinct_absolute_boundaries() -> None:
    now = datetime(2026, 10, 25, 3, tzinfo=UTC)
    samples = (
        record("first", at=datetime(2026, 10, 25, 0, 30, tzinfo=UTC)),
        record("second", at=datetime(2026, 10, 25, 1, 30, tzinfo=UTC)),
    )
    summary = summarize((source(*samples),), now=now, days=1, timezone="Europe/Berlin")
    assert [period["start"] for period in summary["periods"]] == [
        "2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z",
    ]
    assert [period["label"] for period in summary["periods"]] == ["02:00 CEST", "02:00 CET"]


def test_past_24_hours_includes_the_prior_evening_near_local_midnight() -> None:
    now = datetime(2026, 10, 15, 23, tzinfo=UTC)  # 01:00 local on October 16.
    evening = record(at=datetime(2026, 10, 15, 19, tzinfo=UTC))
    summary = summarize((source(evening),), now=now, days=1, timezone="Europe/Berlin")
    assert summary["since"] == "2026-10-14T23:00:00Z"
    assert summary["totals"]["record_count"] == 1


def test_past_24_hours_is_elapsed_time_across_a_dst_change() -> None:
    now = datetime(2026, 10, 25, 3, 15, tzinfo=UTC)
    inside = record("inside", at=now - timedelta(hours=24) + timedelta(minutes=1))
    outside = record("outside", at=now - timedelta(hours=24, seconds=1))
    summary = summarize((source(inside, outside),), now=now, days=1, timezone="Europe/Berlin")
    assert summary["since"] == "2026-10-24T03:15:00Z"
    assert summary["totals"]["record_count"] == 1
    assert summary["periods"][0]["start"] == summary["since"]


def test_absolute_hour_buckets_do_not_overlap_at_a_half_hour_dst_change() -> None:
    now = datetime(2026, 10, 3, 18, tzinfo=UTC)
    samples = (
        record("before", at=datetime(2026, 10, 3, 15, 45, tzinfo=UTC)),
        record("after", at=datetime(2026, 10, 3, 16, 15, tzinfo=UTC)),
    )
    summary = summarize((source(*samples),), now=now, days=1, timezone="Australia/Lord_Howe")
    first, second = summary["periods"]
    assert first["end"] == second["start"] == "2026-10-03T16:00:00Z"


@pytest.fixture
def local_service(tmp_db: Connection, tmp_path: Path) -> tuple[RecordedUsageService, Path, Path]:
    path = tmp_path / "codex" / "sessions" / "sample.jsonl"
    path.parent.mkdir(parents=True)
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    service = RecordedUsageService(
        lambda: connect(str(database)), sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
    )
    return service, path, database


def test_read_is_stored_only_and_refresh_does_not_reparse_unchanged_files(
    local_service: tuple[RecordedUsageService, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("first"))
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 0
    service.refresh()

    def refuse_projection(provider: str, line: str) -> str | None:
        raise AssertionError("An unchanged source was parsed")

    monkeypatch.setattr(usage_service, "project_usage_line", refuse_projection)
    service.refresh()
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["total_tokens"] == 105
    assert summary["sources"][0]["state"] == "ready"


def test_append_projects_only_new_lines_and_persists_no_content(
    local_service: tuple[RecordedUsageService, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, path, database = local_service
    secret = "PRIVATE-PROMPT-AUTH-TOOL-CONTENT"
    path.write_text(json.dumps({"type": "user", "content": secret}) + "\n" + native_line("first"))
    service.refresh()
    projected: list[str] = []
    original = project_usage_line

    def observe(provider: UsageProvider, line: str) -> str | None:
        projected.append(line)
        return original(provider, line)

    monkeypatch.setattr(usage_service, "project_usage_line", observe)
    with path.open("a") as stream:
        stream.write(native_line("second"))
    service.refresh()
    assert all(secret not in line for line in projected)
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 2
    conn = connect(str(database))
    try:
        stored = " ".join(row[0] for row in conn.execute("SELECT snapshot FROM native_usage_files"))
        assert secret not in stored
        assert '"content"' not in stored
        assert '"auth"' not in stored
    finally:
        conn.close()


def test_partial_tail_is_retried_and_never_counted_twice(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, _ = local_service
    complete = native_line("first")
    path.write_text(complete[:-7])
    assert service.refresh()[0]["state"] == "partial"
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 0
    with path.open("a") as stream:
        stream.write(complete[-7:])
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1


def test_bounds_and_missing_source_preserve_verified_records(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, database = local_service
    path.write_text(native_line("first"))
    service.refresh()
    bounded = RecordedUsageService(
        lambda: connect(str(database)), sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW, limits=ScanLimits(max_file_bytes=10),
    )
    assert bounded.refresh()[0]["state"] == "partial"
    assert bounded.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105
    path.unlink()
    path.parent.rmdir()
    assert service.refresh()[0]["state"] == "unavailable"
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105


def test_conflicting_append_stays_excluded_until_verified_clean_rewrite(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("first"))
    service.refresh()
    with path.open("a") as stream:
        stream.write(native_line("first", count=200))
    service.refresh()
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 0
    assert summary["sources"][0]["state"] == "partial"
    path.write_text(native_line("first", count=200))
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 205


@pytest.mark.parametrize("replacement", ["overwrite", "replace", "truncate"])
def test_clean_native_replacement_keeps_distinct_previously_owned_responses(
    local_service: tuple[RecordedUsageService, Path, Path], replacement: str
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("old-response"))
    service.refresh()
    if replacement == "replace":
        replacing = path.with_suffix(".new")
        replacing.write_text(native_line("new-response"))
        replacing.replace(path)
    else:
        if replacement == "truncate":
            path.write_text("")
            service.refresh()
            assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1
        path.write_text(native_line("new-response"))
    service.refresh()
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 2
    assert summary["totals"]["total_tokens"] == 210
    assert summary["sources"][0]["state"] == "ready"
    # A later append uses the new file cursor without losing the retained old response.
    with path.open("a") as stream:
        stream.write(native_line("next-response"))
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 315


def test_clean_conflict_correction_keeps_other_owned_responses_and_modern_threads(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, database = local_service
    path.write_text(native_line("retained") + native_line("corrected"))
    service.refresh()
    with path.open("a") as stream:
        stream.write(native_line("corrected", count=200))
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1
    path.write_text(native_line("corrected", count=200))
    service.refresh()
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 2
    assert summary["totals"]["total_tokens"] == 310
    assert summary["sources"][0]["state"] == "ready"
    # Even a clean empty replacement keeps the observed modern-thread provenance.
    path.write_text("")
    service.refresh()
    conn = connect(str(database))
    try:
        cached = json.loads(conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0])
        assert cached["parsed"]["modern_threads"] == ["thread"]
    finally:
        conn.close()
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 310


def test_clean_replacement_without_conflicted_key_cannot_silently_clear_it(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("conflicted"))
    service.refresh()
    with path.open("a") as stream:
        stream.write(native_line("conflicted", count=200))
    service.refresh()
    path.write_text(native_line("distinct"))
    service.refresh()
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 1
    assert summary["totals"]["total_tokens"] == 105
    assert summary["sources"][0]["state"] == "partial"


def test_same_signature_interior_rewrite_checks_the_full_consumed_digest(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, database = local_service
    outer = json.dumps({"type": "user", "content": "unretained-text" * 1000}) + "\n"
    path.write_text(outer + native_line("old-response") + outer)
    service.refresh()
    path.write_text(outer + native_line("new-response") + outer)
    # Model the same stat signature observed with immediate same-size writes. The
    # native contents change only in the interior, beyond both short end guards.
    stat = path.stat()
    conn = connect(str(database))
    try:
        cached = json.loads(conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0])
        cached.update(mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)
        conn.execute("UPDATE native_usage_files SET snapshot=?", (json.dumps(cached),))
        conn.commit()
    finally:
        conn.close()
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 210


def test_copied_files_count_once_and_cross_file_conflicts_exclude_identity(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("first"))
    copy = path.with_name("copy.jsonl")
    copy.write_text(path.read_text())
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1
    copy.write_text(native_line("first", count=200))
    service.refresh()
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 0


def test_changed_append_validates_the_consumed_prefix_before_reusing_cache(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("first"))
    service.refresh()
    # The old portion changes in place, followed by a valid new native response.
    path.write_text(native_line("first", count=200) + native_line("second"))
    service.refresh()
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["total_tokens"] == 310
    assert summary["totals"]["record_count"] == 2


def test_file_count_and_byte_limits_report_partial_without_zero_fill(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    _, path, database = local_service
    path.write_text(native_line("first"))
    path.with_name("copy.jsonl").write_text(native_line("second"))
    bounded = RecordedUsageService(
        lambda: connect(str(database)), sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW, limits=ScanLimits(max_files=1),
    )
    assert bounded.refresh()[0]["state"] == "partial"
    assert bounded.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1
    tiny = RecordedUsageService(
        lambda: connect(str(database)), sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW, limits=ScanLimits(max_total_bytes=1),
    )
    assert tiny.refresh()[0]["state"] == "partial"
    assert tiny.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1


def test_line_and_time_limits_and_symlinks_are_explicit_partial_sources(
    local_service: tuple[RecordedUsageService, Path, Path]
) -> None:
    _, path, database = local_service
    path.write_text(native_line("first"))
    for limits in (ScanLimits(max_line_bytes=30), ScanLimits(max_seconds=0)):
        bounded = RecordedUsageService(
            lambda: connect(str(database)),
            sources=lambda: (UsageSource("codex", path.parents[1]),),
            now=lambda: NOW, limits=limits,
        )
        assert bounded.refresh()[0]["state"] == "partial"
        assert bounded.summary(days=7, timezone="UTC")["totals"]["total_tokens"] is None
    path.with_name("linked.jsonl").symlink_to(path)
    normal = RecordedUsageService(
        lambda: connect(str(database)), sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
    )
    assert normal.refresh()[0]["state"] == "partial"


def test_parser_failure_keeps_previously_verified_usage(
    local_service: tuple[RecordedUsageService, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, path, _ = local_service
    path.write_text(native_line("first"))
    service.refresh()
    with path.open("a") as stream:
        stream.write(native_line("second"))

    def refuse_parser(_: object) -> object:
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(usage_service, "parse_codex", refuse_parser)
    assert service.refresh()[0]["state"] == "failed"
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105


def test_source_resolution_uses_configured_backend_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert configured_sources() == (
        UsageSource("codex", tmp_path / "codex"), UsageSource("claude", tmp_path / "claude"),
    )
