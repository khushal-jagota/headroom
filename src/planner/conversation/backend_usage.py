"""Explicit, provider-neutral rolling-window usage for agent backends.

Usage is deliberately not part of a backend snapshot.  A snapshot is an ordinary read
that surfaces all over Panels; acquiring usage can make a provider request and, for
Codex, spend a very small amount of allowance.  The only public operation here is named
``refresh`` so composition cannot accidentally turn it into ambient polling.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

import httpx

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import (
    BackendProbeEnvironment,
    SubprocessBackendProbeEnvironment,
)


class BackendUsageOutcome(StrEnum):
    succeeded = "succeeded"
    unavailable = "unavailable"
    unauthenticated = "unauthenticated"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class BackendUsageWindow:
    """One rolling allowance window, named for people rather than providers."""

    name: str
    used_percent: float
    resets_at: datetime


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
            return await adapter.refresh()


# --- Codex -------------------------------------------------------------------------------


CODEX_USAGE_FRESHNESS: Final = timedelta(minutes=10)
CODEX_USAGE_REFRESH_TIMEOUT_SECONDS: Final = 120.0
CODEX_USAGE_REFRESH_MODEL: Final = "gpt-5.6-luna"
_CODEX_REFRESH_PROMPT: Final = "Do not use tools. Reply with OK."
_CODEX_REFRESH_DISABLED_FEATURES: Final = (
    "shell_tool",
    "unified_exec",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "in_app_browser",
    "standalone_web_search",
    "computer_use",
    "apps",
    "image_generation",
    "skill_search",
    "skill_mcp_dependency_install",
    "plugins",
    "remote_plugin",
    "multi_agent",
    "multi_agent_v2",
    "code_mode",
    "code_mode_host",
    "code_mode_only",
)


@dataclass(frozen=True, slots=True)
class _CodexRateLimitSnapshot:
    observed_at: datetime
    windows: tuple[BackendUsageWindow, ...]


class CodexUsageAdapter:
    """Read Codex rollout rate limits, spending only when the local reading is stale."""

    def __init__(
        self,
        *,
        environment: BackendProbeEnvironment | None = None,
        rollout_root: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._environment = environment or SubprocessBackendProbeEnvironment()
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self._rollout_root = rollout_root or codex_home / "sessions"
        self._now = now or (lambda: datetime.now(UTC))

    async def refresh(self) -> BackendUsageResult:
        now = _as_utc(self._now())
        before = newest_codex_rate_limit_snapshot(self._rollout_root)
        if before is not None and timedelta(0) <= now - before.observed_at <= CODEX_USAGE_FRESHNESS:
            return _codex_success(before)

        executable = self._environment.executable_path("codex")
        if executable is None:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=BackendUsageOutcome.unavailable,
                detail="Codex is not installed or not on PATH.",
            )
        # Keep the refresh away from the server's repository and instructions. The CLI
        # still uses the real CODEX_HOME for its existing login and for the rollout that
        # carries the new rate-limit event; --ephemeral would prevent that event.
        with tempfile.TemporaryDirectory(prefix="panels-codex-usage-") as work_directory:
            outcome = await self._environment.run(
                codex_usage_refresh_command(executable, Path(work_directory)),
                timeout_seconds=CODEX_USAGE_REFRESH_TIMEOUT_SECONDS,
            )
        if not outcome.succeeded:
            said = f"{outcome.standard_output}\n{outcome.standard_error}".lower()
            unauthenticated = "not logged in" in said or "login" in said and "required" in said
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
        after = newest_codex_rate_limit_snapshot(self._rollout_root)
        if after is None or (before is not None and after.observed_at <= before.observed_at):
            return BackendUsageResult(
                backend_key=ConversationBackendKey.codex,
                outcome=BackendUsageOutcome.failed,
                detail="Codex finished, but did not write a new usable usage reading.",
            )
        return _codex_success(after)


def codex_usage_refresh_command(executable: str, work_directory: Path) -> tuple[str, ...]:
    """Build the isolated request that causes Codex to persist one rate-limit event."""

    command = (
        executable,
        "exec",
        "--cd",
        str(work_directory),
        "--ignore-user-config",
        "--ignore-rules",
        "--model",
        CODEX_USAGE_REFRESH_MODEL,
        "--config",
        'model_reasoning_effort="low"',
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
    )
    disabled_features = tuple(
        part for feature in _CODEX_REFRESH_DISABLED_FEATURES for part in ("--disable", feature)
    )
    return (*command, *disabled_features, _CODEX_REFRESH_PROMPT)


def newest_codex_rate_limit_snapshot(root: Path) -> _CodexRateLimitSnapshot | None:
    """Read the newest valid ``rate_limits`` event from Codex JSONL rollouts."""

    newest: _CodexRateLimitSnapshot | None = None
    if not root.is_dir():
        return None
    for rollout in root.rglob("*.jsonl"):
        try:
            lines = rollout.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            parsed = _parse_codex_rate_limit_event(event)
            if parsed is not None and (newest is None or parsed.observed_at > newest.observed_at):
                newest = parsed
    return newest


def _parse_codex_rate_limit_event(event: Any) -> _CodexRateLimitSnapshot | None:
    if not isinstance(event, dict):
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None
    observed_at = _parse_datetime(event.get("timestamp"))
    if observed_at is None:
        return None
    windows: list[BackendUsageWindow] = []
    for provider_name in ("primary", "secondary"):
        window = rate_limits.get(provider_name)
        if not isinstance(window, dict):
            continue
        used_percent = _percentage(window.get("used_percent"))
        resets_at = _parse_datetime(window.get("resets_at"))
        minutes = window.get("window_minutes")
        if (
            used_percent is None
            or resets_at is None
            or isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes <= 0
        ):
            continue
        windows.append(
            BackendUsageWindow(
                name=_duration_name(int(minutes)),
                used_percent=used_percent,
                resets_at=resets_at,
            )
        )
    if not windows:
        return None
    return _CodexRateLimitSnapshot(observed_at=observed_at, windows=tuple(windows))


def _codex_success(snapshot: _CodexRateLimitSnapshot) -> BackendUsageResult:
    return BackendUsageResult(
        backend_key=ConversationBackendKey.codex,
        outcome=BackendUsageOutcome.succeeded,
        observed_at=snapshot.observed_at,
        windows=snapshot.windows,
    )


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
        access_token = _read_claude_access_token(self._credential_path)
        if access_token is None:
            return BackendUsageResult(
                backend_key=ConversationBackendKey.claude,
                outcome=BackendUsageOutcome.unauthenticated,
                detail="Claude is not logged in. Run `claude auth login` and try again.",
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


def _read_claude_access_token(path: Path) -> str | None:
    try:
        credentials = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(credentials, dict):
        return None
    oauth = credentials.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = oauth.get("accessToken")
    return token if isinstance(token, str) and token else None


def _parse_claude_windows(body: Any) -> tuple[BackendUsageWindow, ...] | None:
    if not isinstance(body, dict):
        return None
    windows: list[BackendUsageWindow] = []
    for key, value in body.items():
        if key not in ("five_hour", "seven_day") and not key.startswith("seven_day_"):
            continue
        if value is None:
            continue
        if not isinstance(value, dict):
            return None
        used_percent = _percentage(value.get("utilization"))
        resets_at = _parse_datetime(value.get("resets_at"))
        if used_percent is None or resets_at is None:
            return None
        windows.append(
            BackendUsageWindow(
                name=_claude_window_name(key),
                used_percent=used_percent,
                resets_at=resets_at,
            )
        )
    return tuple(windows)


def _claude_window_name(key: str) -> str:
    if key == "five_hour":
        return "5 hours"
    if key == "seven_day":
        return "7 days"
    suffix = key.removeprefix("seven_day_").replace("_", " ").title()
    return f"7 days · {suffix}"


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


def _duration_name(minutes: int) -> str:
    if minutes > 0 and minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"{days} day" if days == 1 else f"{days} days"
    if minutes > 0 and minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{minutes} minutes"


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
