"""Provider usage fixtures: no real CLI, credential, or network is touched."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from planner.conversation.backend_usage import (
    CLAUDE_USAGE_BETA,
    CLAUDE_USAGE_URL,
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageService,
    ClaudeUsageAdapter,
    CodexUsageAdapter,
    UsageHttpResponse,
    codex_usage_refresh_command,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import CommandOutcome

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


@dataclass
class _Machine:
    executable: str | None = "/fixture/bin/codex"
    outcome: CommandOutcome = CommandOutcome(0, "OK\n", "")
    commands: list[tuple[str, ...]] = field(default_factory=list)
    after_run: object | None = None

    def executable_path(self, name: str) -> str | None:
        assert name == "codex"
        return self.executable

    def configured_executable_path(self, name: str) -> str | None:
        del name
        return None

    def real_path(self, path: str) -> str:
        return path

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        environment_overrides: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del timeout_seconds, environment_overrides
        self.commands.append(tuple(argv))
        if callable(self.after_run):
            self.after_run()
        return self.outcome

    async def latest_released_version(
        self, package_name: str, *, timeout_seconds: float
    ) -> str | None:
        del package_name, timeout_seconds
        return None


def _write_codex_snapshot(
    root: Path,
    observed_at: datetime,
    secondary: dict[str, object] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": observed_at.isoformat().replace("+00:00", "Z"),
        "payload": {
            "rate_limits": {
                "primary": {
                    "used_percent": 12.5,
                    "window_minutes": 300,
                    "resets_at": 1785502800,
                },
                "secondary": secondary,
                "credits": {"has_credits": True},
            }
        },
    }
    with (root / "fixture.jsonl").open("a", encoding="utf-8") as fixture:
        fixture.write(json.dumps(event) + "\n")


def test_a_fresh_codex_rollout_is_read_without_running_codex(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "sessions"
        _write_codex_snapshot(root, NOW - timedelta(minutes=9))
        machine = _Machine()
        result = await CodexUsageAdapter(
            environment=machine, rollout_root=root, now=lambda: NOW
        ).refresh()

        assert result.outcome is BackendUsageOutcome.succeeded
        assert [(window.name, window.used_percent) for window in result.windows] == [
            ("5 hours", 12.5)
        ]
        assert machine.commands == []

    asyncio.run(exercise())


def test_a_future_codex_rollout_is_not_accepted_as_fresh(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "sessions"
        _write_codex_snapshot(root, NOW + timedelta(minutes=1))
        machine = _Machine()

        result = await CodexUsageAdapter(
            environment=machine, rollout_root=root, now=lambda: NOW
        ).refresh()

        assert result.outcome is BackendUsageOutcome.failed
        assert machine.commands == [codex_usage_refresh_command("/fixture/bin/codex")]

    asyncio.run(exercise())


def test_claude_config_dir_is_the_default_credential_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CLI uses CLAUDE_CONFIG_DIR for alternate accounts; usage must follow it.
    async def exercise() -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        (tmp_path / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fixture-secret"}}),
            encoding="utf-8",
        )

        async def get(
            url: str, headers: Mapping[str, str], timeout: float
        ) -> UsageHttpResponse:
            del url, headers, timeout
            return UsageHttpResponse(200, {})

        result = await ClaudeUsageAdapter(http_get=get, now=lambda: NOW).refresh()
        assert result.outcome is BackendUsageOutcome.succeeded

    asyncio.run(exercise())


def test_stale_codex_usage_runs_only_the_minimal_request(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "sessions"
        _write_codex_snapshot(root, NOW - timedelta(minutes=11))
        machine = _Machine()
        machine.after_run = lambda: _write_codex_snapshot(
            root,
            NOW,
            {
                "used_percent": 30,
                "window_minutes": 10080,
                "resets_at": "2026-08-07T12:00:00Z",
            },
        )
        result = await CodexUsageAdapter(
            environment=machine, rollout_root=root, now=lambda: NOW
        ).refresh()

        assert result.outcome is BackendUsageOutcome.succeeded
        assert [window.name for window in result.windows] == ["5 hours", "7 days"]
        assert machine.commands == [codex_usage_refresh_command("/fixture/bin/codex")]
        assert "gpt-5.6-luna" in machine.commands[0]
        assert 'model_reasoning_effort="low"' in machine.commands[0]
        assert "read-only" in machine.commands[0]

    asyncio.run(exercise())


def test_codex_malformed_events_and_no_new_snapshot_fail_calmly(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "broken.jsonl").write_text("not json\n{}\n", encoding="utf-8")
        result = await CodexUsageAdapter(
            environment=_Machine(), rollout_root=root, now=lambda: NOW
        ).refresh()
        assert result.outcome is BackendUsageOutcome.failed
        assert "did not write" in (result.detail or "")

    asyncio.run(exercise())


def test_claude_oauth_translates_only_present_windows(tmp_path: Path) -> None:
    async def exercise() -> None:
        credentials = tmp_path / ".credentials.json"
        credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fixture-secret"}}),
            encoding="utf-8",
        )
        calls: list[tuple[str, Mapping[str, str], float]] = []

        async def get(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            calls.append((url, headers, timeout))
            return UsageHttpResponse(
                200,
                {
                    "five_hour": {
                        "utilization": 42.25,
                        "resets_at": "2026-07-31T15:00:00Z",
                    },
                    "seven_day": {
                        "utilization": 11,
                        "resets_at": "2026-08-07T12:00:00Z",
                    },
                    "seven_day_sonnet": None,
                    "seven_day_oauth_apps": {
                        "utilization": 3,
                        "resets_at": "2026-08-07T12:00:00Z",
                    },
                    "extra_usage": {"used_credits": 99},
                },
            )

        result = await ClaudeUsageAdapter(
            credential_path=credentials, http_get=get, now=lambda: NOW
        ).refresh()
        assert result.outcome is BackendUsageOutcome.succeeded
        assert [window.name for window in result.windows] == [
            "5 hours",
            "7 days",
            "7 days · Oauth Apps",
        ]
        assert calls[0][0] == CLAUDE_USAGE_URL
        assert calls[0][1] == {
            "authorization": "Bearer fixture-secret",
            "anthropic-beta": CLAUDE_USAGE_BETA,
        }
        assert "fixture-secret" not in repr(result)

    asyncio.run(exercise())


def test_claude_missing_login_and_unauthorized_are_typed(tmp_path: Path) -> None:
    async def exercise() -> None:
        missing = await ClaudeUsageAdapter(credential_path=tmp_path / "missing").refresh()
        assert missing.outcome is BackendUsageOutcome.unauthenticated

        credentials = tmp_path / "credentials"
        credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "expired"}}), encoding="utf-8"
        )

        async def unauthorized(
            url: str, headers: Mapping[str, str], timeout: float
        ) -> UsageHttpResponse:
            del url, headers, timeout
            return UsageHttpResponse(401, {"error": "expired"})

        result = await ClaudeUsageAdapter(
            credential_path=credentials, http_get=unauthorized
        ).refresh()
        assert result.outcome is BackendUsageOutcome.unauthenticated
        assert "expired" not in repr(result)

    asyncio.run(exercise())


def test_refreshes_serialize_per_backend_but_not_across_backends() -> None:
    class _Held:
        def __init__(self, key: ConversationBackendKey) -> None:
            self.key = key
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.in_flight = 0
            self.maximum = 0

        async def refresh(self) -> BackendUsageResult:
            self.in_flight += 1
            self.maximum = max(self.maximum, self.in_flight)
            self.entered.set()
            await self.release.wait()
            self.in_flight -= 1
            return BackendUsageResult(self.key, BackendUsageOutcome.succeeded)

    async def exercise() -> None:
        codex = _Held(ConversationBackendKey.codex)
        claude = _Held(ConversationBackendKey.claude)
        service = BackendUsageService(
            {ConversationBackendKey.codex: codex, ConversationBackendKey.claude: claude}
        )
        tasks = [
            asyncio.create_task(service.refresh(ConversationBackendKey.codex)),
            asyncio.create_task(service.refresh(ConversationBackendKey.codex)),
            asyncio.create_task(service.refresh(ConversationBackendKey.claude)),
        ]
        await codex.entered.wait()
        await claude.entered.wait()
        assert codex.in_flight == claude.in_flight == 1
        codex.release.set()
        claude.release.set()
        await asyncio.gather(*tasks)
        assert codex.maximum == 1

    asyncio.run(exercise())
