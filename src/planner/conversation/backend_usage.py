"""Explicit, provider-neutral rolling-window usage for agent backends.

Usage is deliberately not part of a backend snapshot. A snapshot is an ordinary read
that surfaces all over Panels, while acquiring usage makes a bounded provider-native
request. The only public operation here is named ``refresh`` so composition cannot
accidentally turn it into ambient polling.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from planner import __version__
from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.adapter import (
    CLIENT_NAME,
    CLIENT_TITLE,
    INITIALIZE_CAPABILITIES,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexRequestRejected,
    child_environment,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import SubprocessBackendProbeEnvironment


class BackendUsageOutcome(StrEnum):
    succeeded = "succeeded"
    unavailable = "unavailable"
    unauthenticated = "unauthenticated"
    failed = "failed"


class BackendUsageWindowKind(StrEnum):
    five_hour = "five_hour"
    seven_day = "seven_day"


@dataclass(frozen=True, slots=True)
class BackendUsageWindow:
    """One rolling allowance window, named for people rather than providers."""

    kind: BackendUsageWindowKind
    used_percent: float
    resets_at: datetime
    model_scope: str | None = None

    @property
    def name(self) -> str:
        duration = "5 hours" if self.kind is BackendUsageWindowKind.five_hour else "7 days"
        return duration if self.model_scope is None else f"{duration} · {self.model_scope}"


@dataclass(frozen=True, slots=True)
class BackendUsageResult:
    backend_key: ConversationBackendKey
    outcome: BackendUsageOutcome
    detail: str | None = None
    observed_at: datetime | None = None
    windows: tuple[BackendUsageWindow, ...] = ()


class BackendUsageAdapter(Protocol):
    async def refresh(self) -> BackendUsageResult: ...


class BackendUsageService:
    """Dispatch explicit refreshes and serialize only requests for the same backend."""

    def __init__(self, adapters: Mapping[ConversationBackendKey, BackendUsageAdapter]) -> None:
        self._adapters = dict(adapters)
        self._locks = {backend_key: asyncio.Lock() for backend_key in ConversationBackendKey}

    async def refresh(self, backend_key: ConversationBackendKey) -> BackendUsageResult:
        async with self._locks[backend_key]:
            adapter = self._adapters.get(backend_key)
            if adapter is None:
                return BackendUsageResult(
                    backend_key=backend_key,
                    outcome=BackendUsageOutcome.unavailable,
                    detail=f"{backend_key.value.title()} does not expose usage to Panels.",
                )
            try:
                return await adapter.refresh()
            except Exception:
                return BackendUsageResult(
                    backend_key=backend_key,
                    outcome=BackendUsageOutcome.failed,
                    detail=f"{backend_key.value.title()} usage could not be refreshed. Try again.",
                )


# --- Codex -------------------------------------------------------------------------------


CODEX_USAGE_REFRESH_TIMEOUT_SECONDS: Final = 30.0


class _CodexRateLimitWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    used_percent: StrictInt = Field(alias="usedPercent")
    window_duration_minutes: StrictInt | None = Field(
        default=None, alias="windowDurationMins"
    )
    resets_at: StrictInt | None = Field(default=None, alias="resetsAt")


class _CodexRateLimitBucket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit_id: str | None = Field(default=None, alias="limitId")
    limit_name: str | None = Field(default=None, alias="limitName")
    primary: _CodexRateLimitWindow | None = None
    secondary: _CodexRateLimitWindow | None = None


class _CodexRateLimitsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rate_limits: _CodexRateLimitBucket = Field(alias="rateLimits")
    rate_limits_by_limit_id: dict[str, _CodexRateLimitBucket] | None = Field(
        default=None, alias="rateLimitsByLimitId"
    )


class _CodexUsageMessageHandler:
    async def on_notification(self, method: str, notification: BaseModel) -> None:
        del method, notification

    async def on_server_request(
        self, method: str, request_id: Any, params: BaseModel
    ) -> None:
        del method, request_id, params

    async def on_child_ended(self) -> None:
        return


class _CodexUsageEnvironment(Protocol):
    def executable_path(self, name: str) -> str | None: ...


class _CodexUsageClient(Protocol):
    async def start(
        self,
        *,
        argv: Sequence[str],
        environment: Mapping[str, str],
        working_directory: Path,
    ) -> None: ...

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any: ...

    async def notify(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> None: ...

    async def stop(self) -> None: ...


type _CodexUsageClientFactory = Callable[..., _CodexUsageClient]


class CodexUsageAdapter:
    """Read Codex rate limits directly from one short-lived app-server child."""

    def __init__(
        self,
        *,
        environment: _CodexUsageEnvironment | None = None,
        now: Callable[[], datetime] | None = None,
        client_factory: _CodexUsageClientFactory = CodexAppServerClient,
    ) -> None:
        self._environment = environment or SubprocessBackendProbeEnvironment()
        self._now = now or (lambda: datetime.now(UTC))
        self._client_factory = client_factory

    async def refresh(self) -> BackendUsageResult:
        executable = self._environment.executable_path("codex")
        if executable is None:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=BackendUsageOutcome.unavailable,
                detail="Codex is not installed or not on PATH.",
            )
        client = self._client_factory(
            handler=_CodexUsageMessageHandler(), description="usage-read"
        )
        try:
            response = await asyncio.wait_for(
                _read_codex_rate_limits(client, executable),
                timeout=CODEX_USAGE_REFRESH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=BackendUsageOutcome.failed,
                detail="Codex usage did not answer in time. Try again.",
            )
        except CodexRequestRejected as rejected:
            unauthenticated = rejected.code in (401, 403) or any(
                phrase in rejected.message.lower()
                for phrase in (
                    "not logged in",
                    "login required",
                    "authentication required",
                    "unauthorized",
                )
            )
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=(
                    BackendUsageOutcome.unauthenticated
                    if unauthenticated
                    else BackendUsageOutcome.failed
                ),
                detail=(
                    "Codex is not logged in. Run `codex login` and try again."
                    if unauthenticated
                    else "Codex could not refresh its usage. Try again."
                ),
            )
        except (CodexAppServerError, ValidationError):
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=BackendUsageOutcome.failed,
                detail="Codex could not refresh its usage. Try again.",
            )
        finally:
            await client.stop()
        windows = _codex_windows(response)
        if not windows:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=BackendUsageOutcome.failed,
                detail="Codex returned usage in a format Panels does not understand.",
            )
        return BackendUsageResult(
            backend_key=ConversationBackendKey.codex,
            outcome=BackendUsageOutcome.succeeded,
            observed_at=_as_utc(self._now()),
            windows=windows,
        )


async def _read_codex_rate_limits(
    client: _CodexUsageClient, executable: str
) -> _CodexRateLimitsResponse:
    await client.start(
        argv=(executable, "app-server"),
        environment=child_environment(),
        working_directory=Path.home(),
    )
    await client.request(
        "initialize",
        bindings.InitializeParams(
            clientInfo=bindings.ClientInfo(
                name=CLIENT_NAME, title=CLIENT_TITLE, version=__version__
            ),
            capabilities=INITIALIZE_CAPABILITIES,
        ).model_dump(mode="json", exclude_none=True, by_alias=True),
    )
    await client.notify("initialized")
    answered = await client.request("account/rateLimits/read", {})
    return _CodexRateLimitsResponse.model_validate(answered)


def _codex_windows(response: _CodexRateLimitsResponse) -> tuple[BackendUsageWindow, ...]:
    windows = list(_codex_bucket_windows(response.rate_limits, model_scope=None))
    account_limit_id = response.rate_limits.limit_id
    for limit_id, bucket in (response.rate_limits_by_limit_id or {}).items():
        if account_limit_id is not None and (
            limit_id == account_limit_id or bucket.limit_id == account_limit_id
        ):
            continue
        model_scope = bucket.limit_name or limit_id
        windows.extend(_codex_bucket_windows(bucket, model_scope=model_scope))
    return tuple(windows)


def _codex_bucket_windows(
    bucket: _CodexRateLimitBucket, *, model_scope: str | None
) -> tuple[BackendUsageWindow, ...]:
    windows: list[BackendUsageWindow] = []
    for provider_window in (bucket.primary, bucket.secondary):
        if provider_window is None:
            continue
        window_kind = _window_kind(provider_window.window_duration_minutes)
        used_percent = _percentage(provider_window.used_percent)
        resets_at = _parse_datetime(provider_window.resets_at)
        if window_kind is None or used_percent is None or resets_at is None:
            continue
        windows.append(
            BackendUsageWindow(
                kind=window_kind,
                used_percent=used_percent,
                resets_at=resets_at,
                model_scope=model_scope,
            )
        )
    return tuple(windows)


# --- Claude ------------------------------------------------------------------------------


CLAUDE_USAGE_URL: Final = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_USAGE_TIMEOUT_SECONDS: Final = 20.0
CLAUDE_USAGE_BETA: Final = "oauth-2025-04-20"


@dataclass(frozen=True, slots=True)
class UsageHttpResponse:
    status_code: int
    body: Any


type UsageHttpGet = Callable[[str, Mapping[str, str], float], Awaitable[UsageHttpResponse]]


async def _http_get_usage(
    url: str, headers: Mapping[str, str], timeout_seconds: float
) -> UsageHttpResponse:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=dict(headers))
    except httpx.HTTPError as error:
        raise RuntimeError("usage request failed") from error
    try:
        body = response.json()
    except ValueError:
        body = None
    return UsageHttpResponse(status_code=response.status_code, body=body)


class ClaudeUsageAdapter:
    """Read Claude's current OAuth allowance using the CLI's existing login."""

    def __init__(
        self,
        *,
        credential_path: Path | None = None,
        http_get: UsageHttpGet = _http_get_usage,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        claude_config = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        self._credential_path = credential_path or claude_config / ".credentials.json"
        self._http_get = http_get
        self._now = now or (lambda: datetime.now(UTC))

    async def refresh(self) -> BackendUsageResult:
        access_token, token_expired = _read_claude_access_token(
            self._credential_path, now=_as_utc(self._now())
        )
        if access_token is None:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.unauthenticated,
                detail=(
                    "Claude's login has expired. Run `claude auth login` and try again."
                    if token_expired
                    else "Claude is not logged in. Run `claude auth login` and try again."
                ),
            )
        try:
            response = await self._http_get(
                CLAUDE_USAGE_URL,
                {
                    "authorization": f"Bearer {access_token}",
                    "anthropic-beta": CLAUDE_USAGE_BETA,
                },
                CLAUDE_USAGE_TIMEOUT_SECONDS,
            )
        except Exception:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.failed,
                detail="Claude usage could not be reached. Try again.",
            )
        if response.status_code in (401, 403):
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.unauthenticated,
                detail="Claude's login is no longer valid. Run `claude auth login` and try again.",
            )
        if response.status_code == 429:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.failed,
                detail="Claude usage is rate-limited. Try again later.",
            )
        if response.status_code < 200 or response.status_code >= 300:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.failed,
                detail="Claude usage could not be refreshed. Try again.",
            )
        windows = _parse_claude_windows(response.body)
        if windows is None:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.failed,
                detail="Claude returned usage in a format Panels does not understand.",
            )
        return BackendUsageResult(
            backend_key=ConversationBackendKey.claude,
            outcome=BackendUsageOutcome.succeeded,
            observed_at=_as_utc(self._now()),
            windows=windows,
        )


def _read_claude_access_token(path: Path, *, now: datetime) -> tuple[str | None, bool]:
    try:
        credentials = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    if not isinstance(credentials, dict):
        return None, False
    oauth = credentials.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None, False
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None, False
    expires_at = oauth.get("expiresAt")
    if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        try:
            expiry = datetime.fromtimestamp(float(expires_at) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            expiry = None
        if expiry is not None and expiry <= now:
            return None, True
    return token, False


def _parse_claude_windows(body: Any) -> tuple[BackendUsageWindow, ...] | None:
    if not isinstance(body, dict):
        return None

    scoped_windows = _parse_claude_limits(body.get("limits"))
    if scoped_windows:
        return tuple(scoped_windows)

    windows: list[BackendUsageWindow] = []
    for key, value in body.items():
        window = _parse_claude_window(key, value)
        if window is not None:
            windows.append(window)
    return tuple(windows) if windows else None


def _parse_claude_limits(value: Any) -> list[BackendUsageWindow]:
    if not isinstance(value, list):
        return []
    windows: list[BackendUsageWindow] = []
    for entry in value:
        window = _parse_claude_limit_entry(entry)
        if window is not None:
            windows.append(window)
    return windows


def _parse_claude_limit_entry(value: Any) -> BackendUsageWindow | None:
    if not isinstance(value, dict):
        return None
    used_percent = _percentage(value.get("percent"))
    if used_percent is None:
        used_percent = _percentage(value.get("utilization"))
    resets_at = _claude_reset_time(value)
    if used_percent is None or resets_at is None:
        return None

    scope = value.get("scope")
    model = scope.get("model") if isinstance(scope, dict) else None
    model_name = (
        model.get("display_name")
        if isinstance(model, dict)
        else None
    )
    if not isinstance(model_name, str) or not model_name:
        model_name = model.get("displayName") if isinstance(model, dict) else None
    if isinstance(model_name, str) and model_name:
        kind = BackendUsageWindowKind.seven_day
        model_scope = model_name
    else:
        group = value.get("group")
        if group == "session":
            kind = BackendUsageWindowKind.five_hour
        elif group in ("weekly", "week", "seven_day"):
            kind = BackendUsageWindowKind.seven_day
        else:
            return None
        model_scope = None
    return BackendUsageWindow(
        kind=kind,
        used_percent=used_percent,
        resets_at=resets_at,
        model_scope=model_scope,
    )


def _parse_claude_window(key: str, value: Any) -> BackendUsageWindow | None:
    if not isinstance(value, dict):
        return None
    used_percent = _percentage(value.get("utilization"))
    resets_at = _claude_reset_time(value)
    if used_percent is None or resets_at is None:
        return None
    return BackendUsageWindow(
        kind=(
            BackendUsageWindowKind.five_hour
            if key == "five_hour"
            else BackendUsageWindowKind.seven_day
        ),
        used_percent=used_percent,
        resets_at=resets_at,
        model_scope=(
            None
            if key in ("five_hour", "seven_day")
            else key.removeprefix("seven_day_").replace("_", " ").title()
        ),
    )


def _claude_reset_time(value: Mapping[str, Any]) -> datetime | None:
    for field in ("resets_at", "reset_at"):
        parsed = _parse_datetime(value.get(field))
        if parsed is not None:
            return parsed
    return None


# --- shared reading ----------------------------------------------------------------------


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _percentage(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0 <= number <= 100 else None


def _window_kind(minutes: int | None) -> BackendUsageWindowKind | None:
    if minutes == 300:
        return BackendUsageWindowKind.five_hour
    if minutes == 10080:
        return BackendUsageWindowKind.seven_day
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def production_backend_usage_service() -> BackendUsageService:
    """Compose the two real adapters; Hermes intentionally has no usage source."""

    environment = SubprocessBackendProbeEnvironment()
    return BackendUsageService(
        {
            ConversationBackendKey.codex: CodexUsageAdapter(environment=environment),
            ConversationBackendKey.claude: ClaudeUsageAdapter(),
        }
    )
