"""Recorded usage agrees with Python without a provider or shared-host rate fetch.

The default Python comparison host can contact LiteLLM on its first summary.
These two direct boundaries instead share tiny native files, inject every rate
answer, reuse the ordinary HTTP apps, and start no listener or browser host.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import (  # type: ignore[attr-defined]
    getproxies_environment,
    proxy_bypass_environment,
)
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from fastapi.testclient import TestClient
from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry
from tests.e2e.conversation_signals_server import NoInstalledBackends
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.conversation import api as conversation_api
from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageWindow,
    BackendUsageWindowKind,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.usage.composition import makeUsageService
from planner.conversation.usage.usagePricing import parseRateTable
from planner.conversation.usage.usageScanCache import (
    decodeScanCache,
    dedupeWithinFile,
    encodeScanCache,
    pruneScanCache,
)
from planner.conversation.usage.UsageService import UsageService
from planner.conversation.usage.usageTranscriptReader import (
    listTranscriptFiles,
    readDirectoryVolumeId,
    readTranscriptRecords,
)
from planner.conversation.usage.usageTranscripts import (
    parseClaudeLine,
    parseTimestampMs,
)
from planner.core.clock import TestClock as FakeClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

ROOT = Path(__file__).resolve().parents[3]
NOW = "2026-11-12T12:00:00Z"
NOW_MS = int(datetime.fromisoformat(NOW).timestamp() * 1000)


def _line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _claude(
    model: str, identifier: str, timestamp: str, usage: Any, **extra: Any
) -> str:
    return _line(
        {
            "type": "assistant",
            "timestamp": timestamp,
            "sessionId": "claude-session",
            "requestId": "request-" + identifier,
            "message": {"id": identifier, "model": model, "usage": usage},
            **extra,
        }
    )


def _codex(tokens: int | float, timestamp: str) -> str:
    return _line(
        {
            "type": "event_msg",
            "timestamp": timestamp,
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": tokens,
                        "cached_input_tokens": 10,
                        "cache_write_input_tokens": 5,
                        "output_tokens": 8,
                        "reasoning_output_tokens": 3,
                    }
                },
            },
        }
    )


def _sources(
    root: Path,
) -> tuple[list[dict[str, str]], dict[str, str], dict[Path, bytes]]:
    native = root / "native"
    codex = native / "codex"
    claude = native / "claude"
    grok = native / "grok"
    empty = native / "empty"
    for directory in (codex, claude, grok, empty):
        directory.mkdir(parents=True)
    first = claude / "z-created-first.jsonl"
    second = claude / "a-created-second.jsonl"
    ordinary = {
        "input_tokens": 100,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 10,
        "output_tokens": 15,
    }
    values: dict[Path, bytes] = {
        first: (
            "\n".join(
                [
                    _claude(
                        "anthropic/claude-opus-4",
                        "duplicate",
                        "2026-11-11T23:45:00Z",
                        ordinary,
                    ),
                    _claude(
                        "ignored-within-file",
                        "duplicate",
                        "2026-11-11T23:45:00Z",
                        {"input_tokens": 999},
                    ),
                    _claude("zero-usage", "zero", "2026-11-12T00:45:00Z", {}),
                    _claude("array-invalid", "array", "2026-11-12T00:45:00Z", []),
                    _claude("bad-date", "bad-date", "2026-02-30T00:00:00Z", ordinary),
                    _claude(
                        "ä-model",
                        "unicode",
                        "2026-11-12T00:15:00Z",
                        {"input_tokens": 1, "output_tokens": 1},
                        costUSD=0,
                    ),
                    _claude(
                        "z-model",
                        "ascii",
                        "2026-11-12T00:15:00Z",
                        {"input_tokens": 2, "output_tokens": 2},
                    ),
                    _claude(
                        "shared",
                        "ambiguous",
                        "2026-11-12T00:20:00Z",
                        {"input_tokens": 7, "output_tokens": 1},
                    ),
                    _claude(
                        "zero-rate",
                        "free",
                        "2026-11-12T00:20:00Z",
                        {"input_tokens": 2, "output_tokens": 3},
                    ),
                    '{"type":"assistant","usage":',
                ]
            )
            + "\r\n"
        ).encode(),
        second: (
            _claude(
                "ignored-across-files",
                "duplicate",
                "2026-11-11T23:45:00Z",
                {"input_tokens": 9999},
            )
            + "\n"
        ).encode(),
        codex
        / "rollout.jsonl": (
            "\n".join(
                [
                    _line(
                        {
                            "type": "session_meta",
                            "timestamp": "2026-11-11T22:45:00Z",
                            "payload": {"id": "codex-session"},
                        }
                    ),
                    _line({"type": "turn_context", "payload": {"model": "gpt-6.1"}}),
                    _codex(40, "2026-11-11T23:50:00Z"),
                    _codex(40.0, "2026-11-12T00:10:00Z"),
                    _codex(40.0, "2026-11-12T00:11:00Z"),
                    _codex(45, "2026-11-12T00:15:00Z"),
                ]
            )
        ).encode(),
        grok
        / "updates.jsonl": (
            _line(
                {
                    "params": {
                        "sessionId": "grok-session",
                        "_meta": {
                            "agentTimestampMs": parseTimestampMs("2026-11-11T22:45:00Z")
                        },
                        "update": {
                            "sessionUpdate": "turn_completed",
                            "prompt_id": "grok-prompt",
                            "usage": {
                                "inputTokens": 20,
                                "cachedReadTokens": 5,
                                "outputTokens": 7,
                                "costUsdTicks": 100_000_000,
                                "modelUsage": {
                                    "grok-3": {
                                        "inputTokens": 10,
                                        "outputTokens": 3,
                                        "costUsdTicks": 50_000_000,
                                    },
                                    "grok-4": {"inputTokens": 10, "outputTokens": 4},
                                },
                            },
                        },
                    }
                }
            )
            + "\n"
        ).encode(),
    }
    fork = codex / "forked.jsonl"
    values[fork] = (
        "\n".join(
            [
                _line(
                    {
                        "type": "session_meta",
                        "timestamp": "2026-11-12T00:00:00Z",
                        "payload": {"id": "fork-child", "forked_from_id": "parent"},
                    }
                ),
                _line({"type": "turn_context", "payload": {"model": "gpt-6.1"}}),
                _line({"type": "session_meta", "payload": {"id": "parent"}}),
                _codex(100, "2026-11-12T00:00:00.001Z"),
                _codex(200, "2026-11-12T00:00:00.002Z"),
            ]
        )
        + "\n"
    ).encode()
    for path, contents in values.items():
        path.write_bytes(contents)
        os.utime(path, ns=(NOW_MS * 1_000_000 + 123_456,) * 2)
    return (
        [
            {"provider": "codex", "dir": str(codex)},
            {"provider": "claude", "dir": str(claude)},
            {"provider": "grok", "dir": str(grok), "fileName": "updates.jsonl"},
            {"provider": "claude", "dir": str(native / "missing")},
            {"provider": "codex", "dir": str(empty)},
        ],
        {
            "claude": str(first),
            "codex": str(codex / "rollout.jsonl"),
            "grok": str(grok / "updates.jsonl"),
            "fork": str(fork),
        },
        values,
    )


def _node(function: str, spec: dict[str, Any]) -> Any:
    return json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
                "const module=await import(pathToFileURL(process.argv[1]).href); "
                "const spec=JSON.parse(readFileSync(0,'utf8')); "
                "process.stdout.write(JSON.stringify(await module[process.argv[2]](spec)));",
                str(ROOT / "server/conversation/usage/testing/direct.ts"),
                function,
            ],
            cwd=ROOT,
            input=json.dumps(spec),
            text=True,
        )
    )


def _report(
    request: pytest.FixtureRequest,
    name: str,
    started: float,
    expected: Any,
    actual: Any,
) -> None:
    report = {
        "kind": "direct",
        "case": name,
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected


def _reader(spec: dict[str, Any]) -> dict[str, Any]:
    home_paths = [
        str(Path(value["path"]).expanduser() / value["child"])
        for value in spec["homes"]
    ]
    proxy_choices = []
    for value in spec["proxy_inputs"]:
        with patch.dict(os.environ, value["environment"], clear=True):
            proxies = getproxies_environment()
        target = urlsplit(value["target"])
        proxy_choices.append(
            None
            if proxy_bypass_environment(target.netloc, proxies)
            else proxies.get(target.scheme)
        )
    inventory = [
        {
            "provider": source["provider"],
            "files": listTranscriptFiles(
                source["dir"],
                spec["since_ms"],
                {"fileName": source["fileName"]} if "fileName" in source else None,
            ),
            "volume": readDirectoryVolumeId(source["dir"]),
        }
        for source in spec["sources"]
    ]
    codex = Path(spec["selected"]["codex"])
    cold = readTranscriptRecords(str(codex), "codex")
    assert cold is not None
    with codex.open("ab") as output:
        output.write(bytes.fromhex(spec["append_hex"]))
    resumed = readTranscriptRecords(str(codex), "codex", cold["position"])
    assert resumed is not None
    rewritten = bytearray(codex.read_bytes())
    rewritten[resumed["position"]["resumeOffset"] - 8] = 0x20
    codex.write_bytes(rewritten)
    rejected = readTranscriptRecords(str(codex), "codex", resumed["position"])
    assert rejected is not None
    fork_path = spec["selected"]["fork"]
    fork_cold = readTranscriptRecords(fork_path, "codex")
    assert fork_cold is not None
    fork_encoded = encodeScanCache(
        {
            fork_path: {
                "size": Path(fork_path).stat().st_size,
                "mtimeMs": spec["mtime_ms"],
                "provider": "codex",
                "records": fork_cold["records"],
                "tailRecords": fork_cold["tailRecords"],
                "position": fork_cold["position"],
            }
        }
    )
    with Path(fork_path).open("ab") as output:
        output.write(bytes.fromhex(spec["fork_append_hex"]))
    fork_resumed = readTranscriptRecords(
        fork_path, "codex", decodeScanCache(fork_encoded)[fork_path]["position"]
    )
    config = load_config(
        None, env={"PLAN_DB_PATH": spec["composition_db_link"], "PLAN_TEST_MODE": "1"}
    )
    composition_service = makeUsageService(
        config, now=lambda: datetime.fromisoformat(NOW)
    )
    link = Path(spec["composition_db_link"])
    composition = {
        "state_dir": str(composition_service.stateDir),
        "target_created": (link.resolve().parent / "usage").is_dir(),
        "lexical_created": (link.parent / "usage").exists(),
    }
    cache = {}
    for provider in ("claude", "codex", "grok"):
        path = spec["selected"][provider]
        parsed = readTranscriptRecords(path, provider)
        assert parsed is not None
        seen: set[str] = set()
        cache[path] = {
            "size": Path(path).stat().st_size,
            "mtimeMs": spec["mtime_ms"],
            "provider": provider,
            "records": dedupeWithinFile(parsed["records"], seen),
            "tailRecords": dedupeWithinFile(parsed["tailRecords"], seen),
            "position": parsed["position"],
        }
    encoded = encodeScanCache(cache)
    decoded = decodeScanCache(encoded)
    prune = dict(decoded)
    example = next(iter(prune.values()))
    prune[spec["sources"][0]["dir"] + "/aged.jsonl"] = {**example, "mtimeMs": 0}
    prune[spec["sources"][0]["dir"] + "-unwalked/retained.jsonl"] = dict(example)
    removed = pruneScanCache(
        prune,
        {
            "livePaths": {spec["selected"]["claude"], spec["selected"]["grok"]},
            "walkedRoots": [source["dir"] for source in spec["sources"]],
            "windowStartMs": spec["since_ms"],
            "retentionCutoffMs": spec["since_ms"] - 90 * 86400000,
        },
    )
    return {
        "inventory": inventory,
        "fork": {"cold": fork_cold, "encoded": fork_encoded, "resumed": fork_resumed},
        "composition": composition,
        "home_paths": home_paths,
        "proxy_choices": proxy_choices,
        "cold": cold,
        "resumed": resumed,
        "rejected_guard": rejected,
        "encoded": encoded,
        "decoded": decoded,
        "pruning": {"removed": removed, "paths": list(prune)},
        "bad_version_entries": len(decodeScanCache({**encoded, "version": 12})),
        "invalid_timestamp": parseTimestampMs("2026-02-30T12:00:00Z"),
        "invalid_claude": parseClaudeLine(spec["invalid_claude"]),
        "array_rate_models": list(
            parseRateTable([{"input_cost_per_token": 1, "output_cost_per_token": 2}])
        ),
    }


def test_unit19_usage_reader_and_cache(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    sources, selected, originals = _sources(tmp_path)
    target = tmp_path / "composition-target" / "actual.sqlite"
    target.parent.mkdir()
    target.touch()
    link = tmp_path / "composition-link" / "alias.sqlite"
    link.parent.mkdir()
    link.symlink_to(target)
    spec = {
        "homes": [
            {"path": str(tmp_path) + "/native/link/../codex", "child": "sessions"},
            {
                "path": str(tmp_path) + "/native//./claude/../configured",
                "child": "projects",
            },
        ],
        "proxy_inputs": [
            {
                "target": "https://example.test:443/rates",
                "environment": {
                    "https_proxy": "http://recorded-lower:8081",
                    "no_proxy": "example.test:443",
                },
            },
            {
                "target": "https://raw.githubusercontent.com/rates.json",
                "environment": {"HTTPS_PROXY": "http://recorded-upper:8080"},
            },
            {
                "target": "https://raw.githubusercontent.com/rates.json",
                "environment": {
                    "HTTPS_PROXY": "http://recorded-upper:8080",
                    "https_proxy": "http://recorded-lower:8081",
                },
            },
            {
                "target": "https://raw.githubusercontent.com/rates.json",
                "environment": {
                    "https_proxy": "http://recorded-lower:8081",
                    "no_proxy": ".githubusercontent.com",
                },
            },
        ],
        "composition_db_link": str(link),
        "fork_append_hex": (_codex(300, "2026-11-12T00:00:05Z") + "\n").encode().hex(),
        "sources": sources,
        "selected": selected,
        "since_ms": NOW_MS - 2 * 86400000,
        "mtime_ms": Path(selected["codex"]).stat().st_mtime_ns / 1_000_000,
        "append_hex": ("\n" + _codex(50, "2026-11-12T00:45:00Z") + "\r\n")
        .encode()
        .hex(),
        "invalid_claude": _claude("array-invalid", "array", "2026-11-12T00:45:00Z", []),
    }
    with synthetic_home_environment(homes):
        expected = _reader(spec)
        (target.parent / "usage").rmdir()
        for path, contents in originals.items():
            path.write_bytes(contents)
            os.utime(path, ns=(NOW_MS * 1_000_000 + 123_456,) * 2)
        actual = _node("compare_reader", spec)
    assert expected["fork"]["cold"]["records"] == []
    assert (
        expected["fork"]["cold"]["position"]["codexState"]["suppressingForkCopies"]
        is True
    )
    assert expected["fork"]["resumed"]["resumed"] is True
    assert len(expected["fork"]["resumed"]["records"]) == 1
    assert expected["fork"]["resumed"]["records"][0]["sessionId"] == "fork-child"
    assert expected["composition"] == {
        "state_dir": str(target.parent / "usage"),
        "target_created": True,
        "lexical_created": False,
    }
    assert len(expected["cold"]["records"]) == 2
    assert len(expected["cold"]["tailRecords"]) == 1
    assert expected["resumed"]["resumed"] is True
    assert expected["rejected_guard"]["resumed"] is False
    assert expected["invalid_claude"] is None
    assert expected["array_rate_models"] == []
    _report(request, "unit19-usage-reader", started, expected, actual)


def _rates(multiplier: int) -> dict[str, Any]:
    return {
        "openai/gpt-6.1": {
            "input_cost_per_token": 0.000001 * multiplier,
            "output_cost_per_token": 0.000003 * multiplier,
            "cache_read_input_token_cost": 0.0000002,
            "cache_creation_input_token_cost": 0.000002,
        },
        "anthropic/claude-opus-4": {
            "input_cost_per_token": 0.000005 * multiplier,
            "output_cost_per_token": 0.000025 * multiplier,
            "cache_read_input_token_cost": 0.0000005,
            "cache_creation_input_token_cost": 0.00000625,
        },
        "provider-a/shared": {"input_cost_per_token": 1, "output_cost_per_token": 1},
        "provider-b/shared": {"input_cost_per_token": 2, "output_cost_per_token": 2},
        "zero-rate": {"input_cost_per_token": 0, "output_cost_per_token": 0},
        "invalid-rate": {"input_cost_per_token": True, "output_cost_per_token": 1},
    }


def _operations() -> list[dict[str, Any]]:
    summary = "/api/conversation/usage/summary?sinceDay=2026-11-11&untilDay=2026-11-12"
    hourly = (
        summary + "&timeZone=Europe%2FBerlin&resolution=hour"
        "&sinceTime=2026-11-11T23:30:00Z&untilTime=2026-11-12T01:30:00Z"
    )
    refresh = {"method": "POST", "path": "/api/conversation/usage/rates/refresh"}
    try:
        ZoneInfo("posixrules")
        posixrules_status = 200
    except ZoneInfoNotFoundError:
        posixrules_status = 422
    return [
        {
            "name": "persisted-limits",
            "method": "GET",
            "path": "/api/conversation/usage/limits",
            "status": 200,
        },
        {
            "name": "daily-utc",
            "method": "GET",
            "path": summary + "&timeZone=UTC",
            "status": 200,
        },
        {
            "name": "daily-berlin",
            "method": "GET",
            "path": summary + "&timeZone=Europe%2FBerlin",
            "status": 200,
        },
        {
            "name": "daily-alias",
            "method": "GET",
            "path": summary + "&timeZone=GMT",
            "status": 200,
        },
        {
            "name": "daily-posixrules",
            "method": "GET",
            "path": summary + "&timeZone=posixrules",
            "status": posixrules_status,
        },
        {"name": "hourly-anchor", "method": "GET", "path": hourly, "status": 200},
        {"name": "refresh-floor", **refresh, "status": 200},
        {
            "name": "override-reprice",
            "method": "GET",
            "path": summary,
            "status": 200,
            "settings": {
                "usagePriceOverrides": {
                    "gpt-6.1": {
                        "inputCostPerMillionTokens": 10,
                        "outputCostPerMillionTokens": 20,
                    },
                    "grok-4": {
                        "inputCostPerMillionTokens": 10,
                        "outputCostPerMillionTokens": 20,
                        "cacheReadCostPerMillionTokens": 0,
                        "cacheWriteCostPerMillionTokens": None,
                    },
                }
            },
        },
        {"name": "refresh-new-prices", **refresh, "status": 200, "advance_ms": 60_001},
        {
            "name": "offline-retains-prices",
            **refresh,
            "status": 200,
            "advance_ms": 60_001,
        },
        {
            "name": "disk-cache-adoption",
            "method": "GET",
            "path": summary,
            "status": 200,
            "restart": True,
        },
        {
            "name": "expired-offline-prices",
            "method": "GET",
            "path": summary,
            "status": 200,
            "advance_ms": 86400001,
        },
        {
            "name": "malformed-cache-reloads",
            "method": "GET",
            "path": summary,
            "status": 200,
            "restart": True,
            "corrupt_cache": True,
        },
        {
            "name": "invalid-calendar",
            "method": "GET",
            "path": summary.replace("2026-11-11", "2026-02-30"),
            "status": 422,
        },
        {
            "name": "reversed-window",
            "method": "GET",
            "path": summary.replace("sinceDay=2026-11-11", "sinceDay=2026-11-13"),
            "status": 422,
        },
        {
            "name": "wrong-case-zone",
            "method": "GET",
            "path": summary + "&timeZone=europe%2Fberlin",
            "status": 422,
        },
        {
            "name": "offset-zone",
            "method": "GET",
            "path": summary + "&timeZone=%2B01%3A00",
            "status": 422,
        },
        {
            "name": "invalid-resolution",
            "method": "GET",
            "path": summary + "&resolution=week",
            "status": 422,
        },
        {
            "name": "missing-hour-instants",
            "method": "GET",
            "path": summary + "&resolution=hour",
            "status": 422,
        },
        {
            "name": "absent-conversation-runtime",
            "method": "GET",
            "path": "/api/conversation/usage/limits",
            "status": 503,
            "runtime_available": False,
        },
    ]


def _seed_limits(path: Path) -> None:
    with closing(connect(str(path))) as connection:
        create_schema(connection)
    now = datetime.fromisoformat(NOW)
    state = BackendStateStore(str(path))
    for backend in (ConversationBackendKey.codex, ConversationBackendKey.claude):
        state.keep_successful_usage(
            BackendUsageResult(
                backend_key=backend,
                outcome=BackendUsageOutcome.succeeded,
                observed_at=now,
                windows=(
                    BackendUsageWindow(
                        BackendUsageWindowKind.five_hour,
                        27.5,
                        datetime(2026, 11, 13, 12, tzinfo=UTC),
                    ),
                    BackendUsageWindow(
                        BackendUsageWindowKind.seven_day,
                        61,
                        datetime(2026, 11, 19, 12, tzinfo=UTC),
                        "model-scope",
                    ),
                ),
            )
        )


def _python_http(spec: dict[str, Any], homes: dict[str, str]) -> dict[str, Any]:
    current_time = [spec["now_ms"]]
    settings: list[dict[str, Any]] = [{}]
    calls: list[dict[str, int]] = []

    def rates_fetcher() -> Any:
        result = len(calls)
        calls.append({"now": current_time[0], "result": result})
        return spec["rates"][result] if result < len(spec["rates"]) else None

    def make() -> UsageService:
        return UsageService(
            spec["state_dir"],
            spec["sources"],
            ratesFetcher=rates_fetcher,
            settingsProvider=lambda: settings[0],
            now=lambda: current_time[0],
        )

    current = make()
    registry = ScriptedBackendRegistry()
    config = load_config(
        None,
        env={
            "PLAN_DB_PATH": spec["db_path"],
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
            "PLAN_LOGS_DIR": str(Path(spec["db_path"]).parent / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(
                Path(spec["db_path"]).parent / "dispatcher.lock"
            ),
        },
    )

    def forbidden(*_arguments: Any, **_keywords: Any) -> Any:
        raise AssertionError(
            "Usage proof must not reach the production rates network door"
        )

    with (
        synthetic_home_environment(homes),
        patch("planner.core.server.makeUsageService", return_value=current),
        patch("planner.conversation.usage.UsageService.fetchRates", forbidden),
        patch(
            "planner.conversation.snapshot.SubprocessBackendProbeEnvironment",
            NoInstalledBackends,
        ),
    ):
        app = create_app(
            config,
            FakeClock(datetime.fromisoformat(NOW)),
            lambda: connect(spec["db_path"]),
            backend_child_factories_for_test={
                key: registry.make_child for key in ConversationBackendKey
            },
        )
        app.dependency_overrides[conversation_api._runtime] = lambda: SimpleNamespace(
            backend_state=BackendStateStore(spec["db_path"])
        )
        answers = []
        with TestClient(app) as client:
            for operation in spec["operations"]:
                current_time[0] += operation.get("advance_ms", 0)
                if "settings" in operation:
                    settings[0] = operation["settings"]
                if operation.get("corrupt_cache"):
                    for cache_path in (current.ratesCachePath, current.scanCachePath):
                        original = cache_path.read_bytes()
                        if cache_path == current.ratesCachePath:
                            document = json.loads(original)
                            document["fetchedAtMs"] = current_time[0]
                            original = _line(document).encode()
                        assert b"gpt-6.1" in original
                        cache_path.write_bytes(
                            original.replace(b"gpt-6.1", b"\xffpt-6.1", 1)
                        )
                if operation.get("restart"):
                    current = make()
                    app.state.recorded_usage = current
                if operation.get("runtime_available") is False:
                    original_runtime = app.state.conversation
                    original_override = app.dependency_overrides.pop(
                        conversation_api._runtime
                    )
                    app.state.conversation = None
                    try:
                        response = client.request(
                            operation["method"], operation["path"]
                        )
                    finally:
                        app.state.conversation = original_runtime
                        app.dependency_overrides[conversation_api._runtime] = (
                            original_override
                        )
                else:
                    response = client.request(operation["method"], operation["path"])
                assert response.status_code == operation["status"], (
                    operation["name"],
                    operation["path"],
                    response.text,
                )
                answers.append(
                    {
                        "name": operation["name"],
                        "status": response.status_code,
                        "body": response.json(),
                        "rates_calls": len(calls),
                    }
                )
        asyncio.run(registry.close())
    with closing(sqlite3.connect(spec["db_path"])) as connection:
        connection.row_factory = sqlite3.Row
        rows = {
            table: [
                dict(row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in ("backend_usage_snapshots", "backend_usage_windows")
        }
    return {
        "answers": answers,
        "calls": calls,
        "scan_cache": json.loads(current.scanCachePath.read_text()),
        "rates_cache": json.loads(current.ratesCachePath.read_text()),
        "rows": rows,
    }


def test_unit19_usage_http_and_pricing(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    sources, _selected, _originals = _sources(tmp_path)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes):
        _seed_limits(seed)
    specs = []
    for side in ("python", "typescript"):
        directory = tmp_path / side
        directory.mkdir()
        database = directory / "data.sqlite"
        with (
            closing(sqlite3.connect(seed)) as source,
            closing(sqlite3.connect(database)) as destination,
        ):
            source.backup(destination)
        state = directory / "usage"
        state.mkdir()
        specs.append(
            {
                "db_path": str(database),
                "state_dir": str(state),
                "now": NOW,
                "now_ms": NOW_MS,
                "sources": sources,
                "rates": [_rates(1), _rates(2), None, None, _rates(3)],
                "operations": _operations(),
            }
        )
    expected = _python_http(specs[0], homes)
    with synthetic_home_environment(homes):
        actual = _node("compare_http", specs[1])
    assert expected["answers"][0]["rates_calls"] == 0
    assert set(expected["answers"][0]["body"]) == {"codex", "claude"}
    assert expected["answers"][1]["body"]["buckets"]
    assert expected["answers"][1]["body"]["contractVersion"] == 5
    assert expected["answers"][1]["body"]["sources"][-2]["status"] == "missing"
    assert expected["answers"][1]["body"]["sources"][-1]["status"] == "ok"
    answers = {answer["name"]: answer for answer in expected["answers"]}
    assert answers["offline-retains-prices"]["body"]["status"] == "cached"
    assert answers["disk-cache-adoption"]["body"]["pricing"]["status"] == "cached"
    assert answers["malformed-cache-reloads"]["body"]["pricing"]["status"] == "fresh"
    assert answers["malformed-cache-reloads"]["rates_calls"] == 5
    assert len(expected["calls"]) == 5
    _report(request, "unit19-usage-http", started, expected, actual)
