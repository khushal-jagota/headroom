"""Stored usage has one source, exact identities, and honest calendar/unknown boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import cast

import pytest

from planner.conversation.usage import service as usage_service
from planner.conversation.usage.adapters import project_usage_line
from planner.conversation.usage.adapters import stream as usage_stream
from planner.conversation.usage.contracts import (
    ParsedUsage,
    ScanLimits,
    SourceSnapshot,
    UsageProvider,
    UsageReadProgress,
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
        provider="codex",
        record_key=key,
        format="codex_response",
        occurred_at=at,
        model="model",
        thread_id="thread",
        session_id="session",
        tokens=UsageTokens(100, 20, 10, 80, 0, 2),
        reported_cost_usd=None,
        raw_usage={},
    )


def source(*records: UsageRecord, modern: tuple[str, ...] = ()) -> SourceSnapshot:
    return SourceSnapshot(
        provider="codex",
        source_key="source",
        state="ready",
        observed_at=NOW,
        records=records,
        modern_threads=modern,
    )


def native_line(key: str, *, count: int = 100, model: str = "model") -> str:
    return (
        "\n".join(
            json.dumps(value)
            for value in (
                {"type": "session_meta", "payload": {"id": "thread"}},
                {"type": "turn_context", "payload": {"turn_id": "turn", "model": model}},
                {
                    "type": "token_usage_record",
                    "timestamp": "2026-10-16T17:00:00Z",
                    "payload": {
                        "thread_id": "thread",
                        "turn_id": "turn",
                        "response_id": key,
                        "usage": {
                            "input_tokens": count,
                            "cached_input_tokens": 20,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 5,
                            "reasoning_output_tokens": 0,
                            "total_tokens": count + 5,
                        },
                    },
                },
            )
        )
        + "\n"
    )


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
    assert (
        metrics((replace(known, tokens=replace(known.tokens, reasoning_tokens=None)),))[
            "unknown_records"
        ]
        == 0
    )


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
        now=NOW,
        days=7,
        timezone="UTC",
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
        record(first_key),
        provider="claude",
        format="claude_message",
        thread_id=None,
        raw_usage={"message_id": "message", "request_id": "request"},
    )
    partial = replace(
        first,
        record_key='claude:message:["message",null]',
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
        source(),
        provider="claude",
        conflicted_record_keys=(first_key,),
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
        "2026-10-25T00:00:00Z",
        "2026-10-25T01:00:00Z",
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
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
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

    monkeypatch.setattr(usage_stream, "project_usage_line", refuse_projection)
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

    monkeypatch.setattr(usage_stream, "project_usage_line", observe)
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
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    service, path, database = local_service
    path.write_text(native_line("first"))
    service.refresh()
    bounded = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(max_total_bytes=10),
    )
    assert bounded.refresh()[0]["state"] == "partial"
    assert bounded.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105
    path.unlink()
    path.parent.rmdir()
    assert service.refresh()[0]["state"] == "unavailable"
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105


def test_conflicting_append_stays_excluded_until_verified_clean_rewrite(
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
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
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(native_line("first"))
    path.with_name("copy.jsonl").write_text(native_line("second"))
    bounded = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(max_files=1),
    )
    assert bounded.refresh()[0]["state"] == "partial"
    assert bounded.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1
    tiny = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(max_total_bytes=1),
    )
    assert tiny.refresh()[0]["state"] == "partial"
    assert tiny.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1


def test_line_and_time_limits_and_symlinks_are_explicit_partial_sources(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(native_line("first"))
    # File and line size exclusions are retired. Request work still yields on time.
    for limits in (ScanLimits(max_seconds=0),):
        bounded = RecordedUsageService(
            lambda: connect(str(database)),
            sources=lambda: (UsageSource("codex", path.parents[1]),),
            now=lambda: NOW,
            limits=limits,
        )
        assert bounded.refresh()[0]["state"] == "partial"
        assert bounded.summary(days=7, timezone="UTC")["totals"]["total_tokens"] is None
    path.with_name("linked.jsonl").symlink_to(path)
    normal = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
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

    def refuse_parser(*_: object) -> object:
        raise RuntimeError("source unavailable")

    monkeypatch.setattr(usage_service.CodexUsageParser, "feed", refuse_parser)
    assert service.refresh()[0]["state"] == "failed"
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105


def test_source_resolution_uses_configured_backend_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    assert configured_sources() == (
        UsageSource("codex", tmp_path / "codex"),
        UsageSource("claude", tmp_path / "claude"),
    )


def test_progressive_read_is_fair_and_survives_recreation(
    tmp_path: Path, tmp_db: Connection
) -> None:
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    codex = tmp_path / "codex" / "sessions" / "a.jsonl"
    claude = tmp_path / "claude" / "projects" / "a.jsonl"
    codex.parent.mkdir(parents=True)
    claude.parent.mkdir(parents=True)
    codex.write_text(
        json.dumps({"type": "user", "content": "PRIVATE-BODY" * 1000}) + "\n" + native_line("codex")
    )
    claude.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-10-16T17:00:00Z",
                "requestId": "request",
                "sessionId": "session",
                "message": {
                    "id": "message",
                    "model": "claude",
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )
        + "\n"
    )
    sources = (UsageSource("codex", codex.parents[1]), UsageSource("claude", claude.parents[1]))
    limits = ScanLimits(max_total_bytes=1024, chunk_bytes=128, file_slice_bytes=128)
    for invocation in range(40):
        service = RecordedUsageService(
            lambda: connect(str(database)), sources=lambda: sources, now=lambda: NOW, limits=limits
        )
        results = service.refresh()
        if invocation == 0:
            conn = connect(str(database))
            rows = list(conn.execute("SELECT provider,snapshot FROM native_usage_files"))
            conn.close()
            assert {row["provider"] for row in rows} == {"codex", "claude"}
            assert all("PRIVATE" not in row["snapshot"] for row in rows)
        if all(not cast(UsageReadProgress, result["progress"])["more_work"] for result in results):
            break
    else:
        pytest.fail("Successive bounded reads did not finish")
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 2
    assert summary["totals"]["total_tokens"] == 111
    assert all(result["state"] == "ready" for result in results)


def test_single_large_file_and_line_have_no_size_exclusion(
    tmp_path: Path, tmp_db: Connection
) -> None:
    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    path = tmp_path / "codex" / "sessions" / "large.jsonl"
    path.parent.mkdir(parents=True)
    with path.open("w") as stream:
        stream.write('{"type":"user","content":"')
        for _ in range(65):
            stream.write("x" * (1024 * 1024))
        stream.write('"}\n' + native_line("large"))
    assert path.stat().st_size > 64 * 1024 * 1024
    limits = ScanLimits(max_total_bytes=16 * 1024 * 1024, file_slice_bytes=4 * 1024 * 1024)
    for _ in range(12):
        service = RecordedUsageService(
            lambda: connect(str(database)),
            sources=lambda: (UsageSource("codex", path.parents[1]),),
            now=lambda: NOW,
            limits=limits,
        )
        result = service.refresh()[0]
        if not cast(UsageReadProgress, result["progress"])["more_work"]:
            break
    else:
        pytest.fail("Large-file checkpoints did not finish")
    assert result["state"] == "ready"
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105


def test_provider_order_rotates_even_when_one_slice_uses_entire_budget(
    tmp_path: Path, tmp_db: Connection
) -> None:
    from planner.conversation.usage.store import read_files

    database = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    sources = []
    for provider, directory in (("codex", "sessions"), ("claude", "projects")):
        path = tmp_path / provider / directory / "a.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text('{"type":"user","content":"private"}\n')
        sources.append(UsageSource(cast(UsageProvider, provider), path.parents[1]))
    service = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: sources,
        now=lambda: NOW,
        limits=ScanLimits(max_total_bytes=1, chunk_bytes=1),
    )
    service.refresh()
    service.refresh()
    conn = connect(str(database))
    snapshots = read_files(conn)
    conn.close()
    assert {file.provider for file in snapshots if file.pending} == {"codex", "claude"}


def test_candidate_generation_change_cannot_publish_already_verified_old_blocks(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    original = native_line("old") + json.dumps({"type": "user", "content": "x" * 4000}) + "\n"
    path.write_text(original)
    limits = ScanLimits(max_total_bytes=len(original) + 512, chunk_bytes=256, file_slice_bytes=256)
    service = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=limits,
    )
    assert cast(UsageReadProgress, service.refresh()[0]["progress"])["more_work"]
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 0
    path.write_text(original.replace('"old"', '"new"'))
    for _ in range(5):
        if not cast(UsageReadProgress, service.refresh()[0]["progress"])["more_work"]:
            break
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 1
    conn = connect(str(database))
    stored = conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0]
    conn.close()
    assert "codex:response:new" in stored
    assert "codex:response:old" not in stored


def test_discovery_bound_is_partial_instead_of_silent_complete(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(native_line("first"))
    path.with_name("second.jsonl").write_text(native_line("second"))
    service = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(max_discovery_entries=1),
    )
    result = service.refresh()[0]
    assert result["state"] == "partial"
    assert any("bounded limit" in issue for issue in result["issues"])


def test_dense_usage_parser_resumes_projection_batches(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    service, path, _ = local_service
    path.write_text("".join(native_line(str(index)) for index in range(300)))
    result = service.refresh()[0]
    assert result["progress"] is not None and result["progress"]["more_work"]
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 0
    for _ in range(6):
        result = service.refresh()[0]
        if result["progress"] is not None and not result["progress"]["more_work"]:
            break
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 300


def test_growing_source_keeps_read_progress_then_finishes_after_pause(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(
        native_line("initial") + json.dumps({"type": "user", "content": "x" * 2048}) + "\n"
    )
    service = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(max_total_bytes=1024, chunk_bytes=128, file_slice_bytes=128),
    )
    offsets: list[int] = []
    for invocation in range(32):
        result = service.refresh()[0]
        if result["progress"] is not None and not result["progress"]["more_work"]:
            break
        conn = connect(str(database))
        cached = json.loads(conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0])
        conn.close()
        offsets.append(cached["pending"]["read_offset"])
        if invocation < 3:
            with path.open("a") as stream:
                stream.write(json.dumps({"type": "user", "content": "new tail"}) + "\n")
    else:
        pytest.fail("The frozen prefix failed to complete after appends paused")
    assert offsets == sorted(offsets)
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 1


def test_native_speed_persists_and_same_identity_speed_conflicts(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    from planner.conversation.usage.store import read_sources

    service, path, database = local_service
    tier = json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "thread_settings_applied",
                "thread_settings": {"service_tier": "priority"},
            },
        }
    )
    path.write_text(tier + "\n" + native_line("first"))
    service.refresh()
    conn = connect(str(database))
    records = read_sources(conn)[0].records
    conn.close()
    assert len(records) == 1 and records[0].speed == "fast"
    copied = replace(records[0], speed="standard")
    summary = summarize((source(records[0], copied),), now=NOW, days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 0
    assert summary["sources"][0]["state"] == "partial"


def test_data_and_progress_rollback_together(tmp_db: Connection) -> None:
    import sqlite3

    from planner.conversation.usage.store import FileSnapshot, save_refresh

    snapshot = FileSnapshot(
        provider="codex",
        source_key="source",
        file_key="file",
        device=1,
        inode=1,
        size=100,
        mtime_ns=1,
        ctime_ns=1,
        offset=0,
        digest="",
        projected_lines=(),
        parsed=ParsedUsage(records=(record(),)),
        pending={"read_offset": 50, "phase": "read"},
    )
    broken_source = SourceSnapshot(
        provider="codex", source_key=cast(str, None), state="partial", observed_at=NOW
    )
    with pytest.raises(sqlite3.IntegrityError):
        save_refresh(tmp_db, (broken_source,), (snapshot,), {"codex": {"queue": ["file"]}})
    assert tmp_db.execute("SELECT count(*) FROM native_usage_files").fetchone()[0] == 0
    assert tmp_db.execute("SELECT count(*) FROM native_usage_sources").fetchone()[0] == 0


def test_rewrite_with_growth_rechecks_earlier_verified_blocks(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    original = native_line("old") + json.dumps({"type": "user", "content": "x" * 4000}) + "\n"
    path.write_text(original)
    service = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(
            max_total_bytes=len(original) + 512,
            chunk_bytes=256,
            file_slice_bytes=len(original) + 512,
        ),
    )
    service.refresh()
    conn = connect(str(database))
    cached = json.loads(conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0])
    conn.close()
    assert cached["pending"]["phase"] == "verify"
    assert cached["pending"]["verify_index"] > 0
    path.write_text(original.replace('"old"', '"new"') + "\n")
    for _ in range(8):
        result = service.refresh()[0]
        if result["progress"] is not None and not result["progress"]["more_work"]:
            break
    summary = service.summary(days=7, timezone="UTC")
    assert summary["totals"]["record_count"] == 1
    conn = connect(str(database))
    cached = json.loads(conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0])
    conn.close()
    assert [record["record_key"] for record in cached["parsed"]["records"]] == [
        "codex:response:new"
    ]


def test_utf8_codepoints_resume_without_retaining_body_bytes(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(
        json.dumps({"type": "user", "content": "private-🦊" * 20}, ensure_ascii=False)
        + "\n"
        + native_line("utf8", model="model-🦊")
    )
    service = RecordedUsageService(
        lambda: connect(str(database)),
        sources=lambda: (UsageSource("codex", path.parents[1]),),
        now=lambda: NOW,
        limits=ScanLimits(max_total_bytes=127, chunk_bytes=7, file_slice_bytes=127),
    )
    for _ in range(40):
        result = service.refresh()[0]
        if result["progress"] is not None and not result["progress"]["more_work"]:
            break
    else:
        pytest.fail("UTF-8 chunk boundaries did not resume")
    assert service.summary(days=7, timezone="UTC")["models"][0]["model"] == "model-🦊"
    conn = connect(str(database))
    cached = conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0]
    conn.close()
    assert "private" not in cached


def test_invalid_utf8_retains_content_free_coverage_issue(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    service, path, database = local_service
    path.write_bytes(
        b'{"type":"user","content":"PRIVATE-INVALID-\xff"}\n' + native_line("valid").encode()
    )
    result = service.refresh()[0]
    assert result["state"] == "partial"
    assert any("not valid UTF-8" in issue for issue in result["issues"])
    assert service.summary(days=7, timezone="UTC")["totals"]["total_tokens"] == 105
    conn = connect(str(database))
    cached = conn.execute("SELECT snapshot FROM native_usage_files").fetchone()[0]
    conn.close()
    assert "PRIVATE-INVALID" not in cached


def test_frozen_prefix_requeues_actual_native_tail_across_restart(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(
        native_line("initial") + json.dumps({"type": "user", "content": "x" * 4000}) + "\n"
    )
    limits = ScanLimits(max_total_bytes=1024, chunk_bytes=256, file_slice_bytes=1024)

    def recreate() -> RecordedUsageService:
        return RecordedUsageService(
            lambda: connect(str(database)),
            sources=lambda: (UsageSource("codex", path.parents[1]),),
            now=lambda: NOW,
            limits=limits,
        )

    recreate().refresh()
    with path.open("a") as stream:
        stream.write(native_line("later"))
    observed_partial_prefix = False
    for _ in range(40):
        service = recreate()
        result = service.refresh()[0]
        count = service.summary(days=7, timezone="UTC")["totals"]["record_count"]
        progress = result["progress"]
        assert progress is not None
        assert progress["bytes_total"] == path.stat().st_size
        if count == 1:
            observed_partial_prefix = True
            assert result["state"] == "partial" and progress["more_work"]
        if not progress["more_work"]:
            assert count == 2 and result["state"] == "ready"
            break
    else:
        pytest.fail("A verified frozen prefix did not catch up to the actual native tail")
    assert observed_partial_prefix


def test_new_file_mid_pass_remains_pending_until_next_pass_reads_it(
    local_service: tuple[RecordedUsageService, Path, Path],
) -> None:
    _, path, database = local_service
    path.write_text(
        native_line("initial") + json.dumps({"type": "user", "content": "x" * 4000}) + "\n"
    )
    limits = ScanLimits(max_total_bytes=1024, chunk_bytes=256, file_slice_bytes=1024)

    def recreate() -> RecordedUsageService:
        return RecordedUsageService(
            lambda: connect(str(database)),
            sources=lambda: (UsageSource("codex", path.parents[1]),),
            now=lambda: NOW,
            limits=limits,
        )

    recreate().refresh()
    path.with_name("new.jsonl").write_text(native_line("new-file"))
    observed_deferred = False
    for _ in range(40):
        service = recreate()
        result = service.refresh()[0]
        count = service.summary(days=7, timezone="UTC")["totals"]["record_count"]
        progress = result["progress"]
        assert progress is not None
        if progress["files_completed"] == 1 and count == 1:
            observed_deferred = True
            assert progress["files_total"] == 2
            assert result["state"] == "partial" and progress["more_work"]
        if not progress["more_work"]:
            assert count == 2 and result["state"] == "ready"
            break
    else:
        pytest.fail("A new eligible file was never reached in the next finite pass")
    assert observed_deferred


@pytest.mark.parametrize("mutation", ["append", "replace", "generation", "new_member"])
def test_completion_invariant_defers_all_known_unvisited_members(
    local_service: tuple[RecordedUsageService, Path, Path],
    mutation: str,
) -> None:
    from planner.conversation.usage.store import read_sources

    _, path, database = local_service
    path.write_text(native_line("initial"))
    second = path.with_name("z-other.jsonl")
    second.write_text(native_line("second"))

    def recreate() -> RecordedUsageService:
        return RecordedUsageService(
            lambda: connect(str(database)),
            sources=lambda: (UsageSource("codex", path.parents[1]),),
            now=lambda: NOW,
            limits=ScanLimits(max_files=1),
        )

    first = recreate().refresh()[0]
    assert first["progress"] is not None and first["progress"]["files_completed"] == 1
    if mutation == "append":
        with path.open("a") as stream:
            stream.write(native_line("changed"))
    elif mutation == "replace":
        replacement = path.with_suffix(".replacement")
        replacement.write_text(native_line("changed"))
        replacement.replace(path)
    elif mutation == "generation":
        before_size = path.stat().st_size
        path.write_text(native_line("changed"))
        assert path.stat().st_size == before_size
    else:
        path.with_name("new.jsonl").write_text(native_line("changed"))

    service = recreate()
    pending = service.refresh()[0]
    progress = pending["progress"]
    assert progress is not None
    assert pending["state"] == "partial" and progress["more_work"]
    assert progress["files_total"] == (3 if mutation == "new_member" else 2)
    assert progress["files_completed"] == (2 if mutation == "new_member" else 1)
    assert progress["bytes_total"] == sum(
        file.stat().st_size for file in path.parent.glob("*.jsonl")
    )
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 2
    for _ in range(8):
        service = recreate()
        result = service.refresh()[0]
        assert result["progress"] is not None
        if not result["progress"]["more_work"]:
            assert result["state"] == "ready"
            break
    else:
        pytest.fail("Latest known inventory work never completed after mutations stopped")
    conn = connect(str(database))
    keys = {record.record_key for record in read_sources(conn)[0].records}
    conn.close()
    assert keys == {"codex:response:initial", "codex:response:second", "codex:response:changed"}
    assert service.summary(days=7, timezone="UTC")["totals"]["record_count"] == 3
