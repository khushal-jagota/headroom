"""Provider usage fixtures: no real CLI, credential, or network is touched."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from planner.conversation.backend_usage import (
    CLAUDE_USAGE_BETA,
    CLAUDE_USAGE_URL,
    BackendUsageOutcome,
    ClaudeUsageAdapter,
    CodexUsageAdapter,
    UsageHttpResponse,
)

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


class _Machine:
    def __init__(self, executable: str | None = "/fixture/bin/codex") -> None:
        self.executable = executable

    def executable_path(self, name: str) -> str | None:
        assert name == "codex"
        return self.executable

    def configured_executable_path(self, name: str) -> str | None:
        del name
        return None

    def real_path(self, path: str) -> str:
        return path

    def user_local_npm_prefix(self) -> str:
        return str(Path.home() / ".local")

    def prefix_is_owned_and_writable(self, prefix: str) -> bool:
        del prefix
        return False

class _CodexClient:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.started: tuple[tuple[str, ...], Path] | None = None
        self.requests: list[tuple[str, object, float | None]] = []
        self.notifications: list[str] = []
        self.events: list[str] = []
        self.stopped = False

    async def start(
        self,
        *,
        argv: Sequence[str],
        environment: Mapping[str, str],
        working_directory: Path,
    ) -> None:
        assert environment
        self.started = (tuple(argv), working_directory)
        self.events.append("start")

    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
    ) -> object:
        self.requests.append((method, params, None))
        self.events.append(method)
        if isinstance(self.answer, Exception) and method == "account/rateLimits/read":
            raise self.answer
        return {} if method == "initialize" else self.answer

    async def notify(
        self, method: str, params: Mapping[str, object] | None = None
    ) -> None:
        del params
        self.notifications.append(method)
        self.events.append(method)

    async def stop(self) -> None:
        self.stopped = True
        self.events.append("stop")


def _codex_adapter(client: _CodexClient) -> CodexUsageAdapter:
    return CodexUsageAdapter(
        environment=_Machine(),
        now=lambda: NOW,
        client_factory=lambda **kwargs: client,
    )


def test_codex_reads_account_and_named_model_windows_without_starting_a_turn() -> None:
    async def exercise() -> None:
        client = _CodexClient(
            {
                "accountId": "discarded-account",
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 12,
                        "windowDurationMins": 300,
                        "resetsAt": 1785502800,
                    },
                    "secondary": {
                        "usedPercent": 30,
                        "windowDurationMins": 10080,
                        "resetsAt": 1786104000,
                    },
                    "credits": {"balance": "discarded"},
                },
                "rateLimitsByLimitId": {
                    "codex": {"limitId": "codex"},
                    "gpt-5.3-codex": {
                        "limitId": "gpt-5.3-codex",
                        "limitName": "GPT-5.3-Codex",
                        "secondary": {
                            "usedPercent": 7,
                            "windowDurationMins": 10080,
                            "resetsAt": 1786104000,
                        },
                    },
                },
                "rateLimitResetCredits": {"credits": ["discarded"]},
            }
        )

        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.succeeded
        assert result.observed_at == NOW
        assert [(window.name, window.used_percent) for window in result.windows] == [
            ("5 hours", 12.0),
            ("7 days", 30.0),
            ("7 days · GPT-5.3-Codex", 7.0),
        ]
        assert client.started == (("/fixture/bin/codex", "app-server"), Path.home())
        assert [request[0] for request in client.requests] == [
            "initialize",
            "account/rateLimits/read",
        ]
        assert client.requests[1] == ("account/rateLimits/read", {}, None)
        assert client.notifications == ["initialized"]
        assert client.stopped is True
        assert client.events == [
            "start",
            "initialize",
            "initialized",
            "account/rateLimits/read",
            "stop",
        ]

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
                        "reset_at": "2026-08-07T12:00:00Z",
                    },
                    "spark": {
                        "utilization": 8,
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
            "7 days · Spark",
        ]
        assert calls[0][0] == CLAUDE_USAGE_URL
        assert calls[0][1] == {
            "authorization": "Bearer fixture-secret",
            "anthropic-beta": CLAUDE_USAGE_BETA,
        }
        assert "fixture-secret" not in repr(result)

    asyncio.run(exercise())


def test_claude_current_scoped_limits_are_preferred_and_partial_entries_are_skipped(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        credentials = tmp_path / "credentials"
        credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fixture-secret"}}),
            encoding="utf-8",
        )

        async def get(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            del url, headers, timeout
            return UsageHttpResponse(
                200,
                {
                    "five_hour": "legacy-invalid-but-ignored",
                    "limits": [
                        {
                            "group": "session",
                            "percent": 42.25,
                            "reset_at": "2026-07-31T15:00:00Z",
                        },
                        {
                            "group": "weekly",
                            "percent": 11,
                            "resets_at": "2026-08-07T12:00:00Z",
                        },
                        {
                            "kind": "model",
                            "percent": 3,
                            "resets_at": "2026-08-07T12:00:00Z",
                            "scope": {"model": {"display_name": "Opus"}},
                        },
                        {"group": "weekly", "percent": "malformed"},
                    ],
                },
            )

        result = await ClaudeUsageAdapter(
            credential_path=credentials, http_get=get, now=lambda: NOW
        ).refresh()

        assert result.outcome is BackendUsageOutcome.succeeded
        assert [(window.name, window.used_percent) for window in result.windows] == [
            ("5 hours", 42.25),
            ("7 days", 11.0),
            ("7 days · Opus", 3.0),
        ]
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


def test_claude_expired_login_is_typed_without_using_the_refresh_token(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        credentials = tmp_path / "credentials"
        credentials.write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "expired-access",
                        "expiresAt": int(NOW.timestamp() * 1000) - 1,
                        "refreshToken": "must-not-be-used",
                    }
                }
            ),
            encoding="utf-8",
        )
        called = False

        async def get(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            nonlocal called
            del url, headers, timeout
            called = True
            return UsageHttpResponse(200, {})

        result = await ClaudeUsageAdapter(
            credential_path=credentials, http_get=get, now=lambda: NOW
        ).refresh()

        assert result.outcome is BackendUsageOutcome.unauthenticated
        assert result.detail == (
            "Claude's login has expired. Run `claude auth login` and try again."
        )
        assert called is False
        assert "expired-access" not in repr(result)
        assert "must-not-be-used" not in repr(result)

    asyncio.run(exercise())
