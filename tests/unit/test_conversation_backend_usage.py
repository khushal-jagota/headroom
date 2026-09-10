"""Provider usage fixtures: no real CLI, credential, or network is touched."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

import planner.conversation.backend_usage as backend_usage
from planner.conversation.backend_usage import (
    CLAUDE_USAGE_BETA,
    CLAUDE_USAGE_URL,
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageService,
    ClaudeUsageAdapter,
    CodexUsageAdapter,
    UsageHttpResponse,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexRequestRejected,
    CodexWireFailed,
)
from planner.conversation.contracts import ConversationBackendKey

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def test_usage_outcomes_keep_the_public_wire_values() -> None:
    assert [outcome.value for outcome in BackendUsageOutcome] == [
        "succeeded",
        "unavailable",
        "unauthenticated",
        "failed",
    ]


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


class _NeverAnswerCodexClient(_CodexClient):
    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
    ) -> object:
        self.requests.append((method, params, None))
        self.events.append(method)
        if method == "account/rateLimits/read":
            await asyncio.Event().wait()
        return {}


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


def test_codex_keeps_named_buckets_when_optional_limit_ids_are_absent() -> None:
    async def exercise() -> None:
        client = _CodexClient(
            {
                "rateLimits": {
                    "secondary": {
                        "usedPercent": 30,
                        "windowDurationMins": 10080,
                        "resetsAt": 1786104000,
                    }
                },
                "rateLimitsByLimitId": {
                    "named-model": {
                        "limitName": "Named Model",
                        "primary": {
                            "usedPercent": 7,
                            "windowDurationMins": 300,
                            "resetsAt": 1785502800,
                        },
                    }
                },
            }
        )

        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.succeeded
        assert [window.name for window in result.windows] == [
            "7 days",
            "5 hours · Named Model",
        ]

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

        async def get(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            del url, headers, timeout
            return UsageHttpResponse(
                200,
                {
                    "five_hour": {
                        "utilization": 1,
                        "reset_at": "2026-07-31T15:00:00Z",
                    }
                },
            )

        result = await ClaudeUsageAdapter(http_get=get, now=lambda: NOW).refresh()
        assert result.outcome is BackendUsageOutcome.succeeded

    asyncio.run(exercise())


def test_codex_skips_absent_and_malformed_optional_windows() -> None:
    async def exercise() -> None:
        client = _CodexClient(
            {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 4,
                        "windowDurationMins": 300,
                        "resetsAt": 1785502800,
                    },
                    "secondary": {
                        "usedPercent": 4,
                        "windowDurationMins": None,
                        "resetsAt": None,
                    },
                    "unrelated": {"changed": True},
                },
                "rateLimitsByLimitId": None,
                "newTopLevelField": "ignored",
            }
        )
        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.succeeded
        assert [window.name for window in result.windows] == ["5 hours"]

    asyncio.run(exercise())


def test_codex_unavailable_is_typed_without_starting_a_child() -> None:
    async def exercise() -> None:
        machine = _Machine(executable=None)
        result = await CodexUsageAdapter(
            environment=machine,
            now=lambda: NOW,
            client_factory=lambda **kwargs: pytest.fail("client must not be created"),
        ).refresh()

        assert result.outcome is BackendUsageOutcome.unavailable

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [CodexWireFailed("ended")])
def test_codex_child_failure_is_typed_and_the_child_stops(failure: Exception) -> None:
    async def exercise() -> None:
        client = _CodexClient(failure)
        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.failed
        assert result.detail == "Codex could not refresh its usage. Try again."
        assert client.stopped is True

    asyncio.run(exercise())


def test_codex_whole_probe_timeout_stops_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(backend_usage, "CODEX_USAGE_REFRESH_TIMEOUT_SECONDS", 0.01)
        client = _NeverAnswerCodexClient({})

        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.failed
        assert result.detail == "Codex usage did not answer in time. Try again."
        assert client.stopped is True

    asyncio.run(exercise())


def test_codex_authentication_rejection_is_typed_without_provider_detail() -> None:
    async def exercise() -> None:
        client = _CodexClient(
            CodexRequestRejected(
                "account/rateLimits/read",
                None,
                "secret codex account authentication required to read rate limits",
            )
        )
        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.unauthenticated
        assert result.detail == "Codex is not logged in. Run `codex login` and try again."
        assert "secret" not in repr(result)
        assert client.stopped is True

    asyncio.run(exercise())




def test_codex_malformed_response_is_typed() -> None:
    async def exercise() -> None:
        client = _CodexClient({"rateLimits": "changed"})
        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.failed
        assert result.detail == "Codex could not refresh its usage. Try again."
        assert client.stopped is True

    asyncio.run(exercise())


@pytest.mark.parametrize("used_percent", [True, "4", 4.5])
def test_codex_numbers_do_not_coerce(used_percent: object) -> None:
    async def exercise() -> None:
        client = _CodexClient(
            {
                "rateLimits": {
                    "primary": {
                        "usedPercent": used_percent,
                        "windowDurationMins": 300,
                        "resetsAt": 1785502800,
                    }
                }
            }
        )
        result = await _codex_adapter(client).refresh()

        assert result.outcome is BackendUsageOutcome.failed
        assert client.stopped is True

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




def test_claude_rate_limit_is_typed_without_echoing_provider_body(tmp_path: Path) -> None:
    async def exercise() -> None:
        credentials = tmp_path / "credentials"
        credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fixture-secret"}}),
            encoding="utf-8",
        )

        async def rate_limited(
            url: str, headers: Mapping[str, str], timeout: float
        ) -> UsageHttpResponse:
            del url, headers, timeout
            return UsageHttpResponse(429, {"error": "fixture-secret provider detail"})

        result = await ClaudeUsageAdapter(
            credential_path=credentials, http_get=rate_limited
        ).refresh()

        assert result.outcome is BackendUsageOutcome.failed
        assert result.detail == "Claude usage is rate-limited. Try again later."
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


@pytest.mark.parametrize("credential_text", ["not json"])
def test_claude_malformed_credentials_are_unauthenticated_without_transport(
    tmp_path: Path, credential_text: str
) -> None:
    async def exercise() -> None:
        credentials = tmp_path / "credentials"
        credentials.write_text(credential_text, encoding="utf-8")
        called = False

        async def get(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            nonlocal called
            del url, headers, timeout
            called = True
            return UsageHttpResponse(200, {})

        result = await ClaudeUsageAdapter(credential_path=credentials, http_get=get).refresh()
        assert result.outcome is BackendUsageOutcome.unauthenticated
        assert called is False

    asyncio.run(exercise())


def test_claude_transport_failure_is_typed(tmp_path: Path) -> None:
    async def exercise() -> None:
        credentials = tmp_path / "credentials"
        credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fixture-secret"}}),
            encoding="utf-8",
        )

        async def fail(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            del url, headers, timeout
            raise RuntimeError("fixture transport failed")

        result = await ClaudeUsageAdapter(credential_path=credentials, http_get=fail).refresh()
        assert result.outcome is BackendUsageOutcome.failed
        assert result.detail == "Claude usage could not be reached. Try again."

    asyncio.run(exercise())


@pytest.mark.parametrize("body", [[]])
def test_claude_malformed_payload_is_typed(tmp_path: Path, body: object) -> None:
    async def exercise() -> None:
        credentials = tmp_path / "credentials"
        credentials.write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "fixture-secret"}}),
            encoding="utf-8",
        )

        async def get(url: str, headers: Mapping[str, str], timeout: float) -> UsageHttpResponse:
            del url, headers, timeout
            return UsageHttpResponse(200, body)

        result = await ClaudeUsageAdapter(credential_path=credentials, http_get=get).refresh()
        assert result.outcome is BackendUsageOutcome.failed
        assert result.detail == "Claude returned usage in a format Panels does not understand."

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
