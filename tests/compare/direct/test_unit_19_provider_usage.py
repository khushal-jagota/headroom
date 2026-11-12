"""Provider limits and credential decisions match Python without external actions.

The route proof injects already parsed allowances. This direct proof covers the
remaining boundary: provider payloads, tiny synthetic login files, and recorded
HTTP answers. It needs no listener or provider process.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation import backend_usage as usage
from planner.conversation.backends.codex_app_server.client import CodexRequestRejected

ROOT = Path(__file__).resolve().parents[3]
NOW = "2026-11-12T10:00:00.000Z"


def _windows_json(windows: Any) -> list[dict[str, Any]] | None:
    if windows is None:
        return None
    return [
        {
            "kind": window.kind.value,
            "used_percent": window.used_percent,
            "resets_at": window.resets_at.astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "model_scope": window.model_scope,
        }
        for window in windows
    ]


def _result_json(result: usage.BackendUsageResult) -> dict[str, Any]:
    return {
        "backend_key": result.backend_key.value,
        "outcome": result.outcome.value,
        "detail": result.detail,
        "observed_at": (
            result.observed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if result.observed_at
            else None
        ),
        "windows": _windows_json(result.windows),
    }


async def _stop_codex_client() -> None:
    return None


async def _python_answers(spec: dict[str, Any]) -> dict[str, Any]:
    refreshes = []
    for test in spec["refreshes"]:
        calls: list[dict[str, Any]] = []

        async def recorded_get(
            url: str,
            headers: Mapping[str, str],
            timeout_seconds: float,
            *,
            case: dict[str, Any] = test,
            recorded: list[dict[str, Any]] = calls,
        ) -> usage.UsageHttpResponse:
            recorded.append(
                {"url": url, "headers": dict(headers), "timeout_seconds": timeout_seconds}
            )
            if case.get("transport_error"):
                raise RuntimeError("Synthetic transport failure")
            return usage.UsageHttpResponse(status_code=case["status_code"], body=case["body"])

        adapter = usage.ClaudeUsageAdapter(
            credential_path=Path(test["credential_path"]),
            http_get=recorded_get,
            now=lambda: datetime.fromisoformat(NOW),
        )
        refreshes.append(
            {"name": test["name"], "result": _result_json(await adapter.refresh()), "calls": calls}
        )
    codex_windows: list[list[dict[str, Any]] | None] = []
    for raw in spec["codex_json"]:
        try:
            response = usage._CodexRateLimitsResponse.model_validate(json.loads(raw))
        except ValidationError:
            codex_windows.append(None)
        else:
            codex_windows.append(_windows_json(usage._codex_windows(response)))
    codex_refreshes = []
    for rejected in (False, True):
        codex_calls: list[dict[str, str]] = []

        async def read_codex_rate_limits(
            client: Any,
            executable: str,
            *,
            recorded: list[dict[str, str]] = codex_calls,
            was_rejected: bool = rejected,
        ) -> Any:
            del client
            recorded.append({"executable": executable})
            if was_rejected:
                raise CodexRequestRejected(
                    "account/rateLimits/read", 401, "Synthetic login required"
                )
            return usage._CodexRateLimitsResponse.model_validate(json.loads(spec["codex_json"][0]))

        with patch.object(usage, "_read_codex_rate_limits", read_codex_rate_limits):
            codex = usage.CodexUsageAdapter(
                environment=SimpleNamespace(executable_path=lambda name: "/synthetic/bin/codex"),
                now=lambda: datetime.fromisoformat(NOW),
                client_factory=lambda **kwargs: SimpleNamespace(stop=_stop_codex_client),
            )
            codex_refreshes.append(
                {"result": _result_json(await codex.refresh()), "calls": codex_calls}
            )
    return {
        "claude_windows": [
            _windows_json(usage._parse_claude_windows(body)) for body in spec["claude_payloads"]
        ],
        "codex_windows": codex_windows,
        "refreshes": refreshes,
        "codex_refreshes": codex_refreshes,
    }


def test_unit19_provider_usage(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    scoped = {
        "limits": [
            {"group": "session", "percent": 12.5, "resets_at": 1794479400},
            {"group": "weekly", "utilization": 73, "reset_at": "2026-11-15T12:30:00+02:00"},
            {
                "group": "unknown",
                "percent": 8,
                "reset_at": "2026-11-15T10:30:00Z",
                "scope": {"model": {"displayName": "Opus 4.1"}},
            },
        ],
        "five_hour": {"utilization": 99, "resets_at": 1794479400},
    }
    legacy = {
        "five_hour": {"utilization": 22.5, "resets_at": "2026-11-12T11:30:00.125Z"},
        "seven_day": {"utilization": 88, "resets_at": 1794738600},
        "seven_day_sonnet_4_5": {"utilization": 47, "reset_at": "2026-11-15T10:30:00Z"},
    }
    rejected = {
        "limits": [
            {"group": "session", "percent": True, "resets_at": 1794479400},
            {"group": "session", "percent": 101, "resets_at": 1794479400},
            {"group": "session", "percent": 1, "resets_at": "2026-02-30T10:00:00Z"},
            {"group": "session", "percent": 1, "resets_at": "2026-11-12T10:00:00"},
            {"group": "session", "percent": 1, "resets_at": True},
        ]
    }
    primary = {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1794479400}
    secondary = {"usedPercent": 80, "windowDurationMins": 10080, "resetsAt": 1794738600}
    codex_payloads = [
        {
            "rateLimits": {"limitId": "account", "primary": primary, "secondary": secondary},
            "rateLimitsByLimitId": {
                "account": {"primary": primary},
                "account-alias": {"limitId": "account", "secondary": secondary},
                "model-key": {"limitName": "Model allowance", "primary": primary},
                "fallback-key": {"secondary": secondary},
            },
        },
        {
            "rateLimits": {
                "primary": {**primary, "windowDurationMins": 42},
                "secondary": {**secondary, "usedPercent": -1},
            }
        },
        {"rateLimits": {"primary": {**primary, "usedPercent": 25.0}}},
        {"rateLimits": {"primary": {**primary, "resetsAt": True}}},
        {"rateLimits": {"primary": "invalid"}},
        {},
    ]
    refreshes = []
    scenarios = [
        ("scoped-success", "valid", 200, scoped, False),
        ("expired-login", "expired", 200, scoped, False),
        ("missing-login", "missing", 200, scoped, False),
        ("invalid-login-file", "malformed", 200, scoped, False),
        ("rejected-login", "valid", 401, None, False),
        ("rate-limited", "valid", 429, None, False),
        ("server-failure", "valid", 500, None, False),
        ("transport-failure", "valid", 200, None, True),
        ("invalid-usage-body", "valid", 200, rejected, False),
    ]
    for name, credential_kind, status_code, body, transport_error in scenarios:
        credential_path = tmp_path / f"{name}.credentials.json"
        if credential_kind == "malformed":
            credential_path.write_text("{")
        elif credential_kind != "missing":
            credential_path.write_text(
                json.dumps(
                    {
                        "claudeAiOauth": {
                            "accessToken": "synthetic-usage-token",
                            "expiresAt": 0 if credential_kind == "expired" else 1893456000000,
                        }
                    }
                )
            )
        refreshes.append(
            {
                "name": name,
                "credential_path": str(credential_path),
                "status_code": status_code,
                "body": body,
                "transport_error": transport_error,
            }
        )
    spec = {
        "now": NOW,
        "claude_payloads": [scoped, legacy, rejected, {"limits": [None], **legacy}, None],
        "codex_json": [json.dumps(body) for body in codex_payloads],
        "refreshes": refreshes,
    }
    with synthetic_home_environment(homes):
        expected = asyncio.run(_python_answers(spec))
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; "
                    "import {pathToFileURL} from 'node:url'; "
                    "const {compare_provider_usage}=await "
                    "import(pathToFileURL(process.argv[1]).href); "
                    "const spec=JSON.parse(readFileSync(0,'utf8')); "
                    "process.stdout.write(JSON.stringify(await compare_provider_usage(spec)));",
                    str(ROOT / "server/conversation/backend_usage/testing/direct.ts"),
                ],
                input=json.dumps(spec),
                cwd=ROOT,
                text=True,
            )
        )
    assert len(expected["codex_windows"][0]) == 4
    assert expected["codex_windows"][1] == []
    assert expected["codex_windows"][2:] == [None] * 4
    assert expected["claude_windows"][2] is None
    assert [len(value["calls"]) for value in expected["refreshes"]] == [1, 0, 0, 0, 1, 1, 1, 1, 1]
    report = {
        "kind": "direct",
        "case": "unit19-provider-usage",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit19-provider-usage.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
