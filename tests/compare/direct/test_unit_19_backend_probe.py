"""Prove the real card probe independently of the HTTP proof's injected probe.

Six small recorded machine descriptions cover provider mappings, saved login,
model metadata, and native/npm/manual advisories. There is no host or provider
process, and the recording environment rejects every unlisted command.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.backends.claude_model_catalog import (
    ClaudeModel,
    ClaudeModelCatalog,
    ClaudeModelCatalogUnavailable,
)
from planner.conversation.backends.codex_app_server.login import (
    CodexLoginGeneration,
    StoredCodexLogin,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModel,
    CodexModelCatalog,
    CodexModelCatalogUnavailable,
)
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.snapshot import (
    CodexLoginStanding,
    CommandOutcome,
    _version_is_newer,
    parse_version,
    probe_backend,
)

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime.fromisoformat("2026-11-12T12:00:00Z")


def outcome(output: str, exit_code: int = 0) -> dict[str, Any]:
    return {"exit_code": exit_code, "standard_output": output, "standard_error": ""}


def fixtures() -> list[dict[str, Any]]:
    codex = {
        "backend": "codex",
        "executable": "/synthetic/bin/codex",
        "real_path": "/synthetic/.codex/packages/standalone/codex",
        "version": outcome("codex-cli 0.145.0"),
        "identity": outcome("Logged in using ChatGPT\n"),
        "writable": False,
        "latest": None,
        "login": {
            "present": True,
            "readable": True,
            "account_label": "synthetic@example.invalid",
            "access_token_expires_at": "2026-11-13T12:00:00Z",
            "generation": {"account_id": "synthetic", "last_refresh": "refresh-one"},
            "rejected": False,
        },
    }
    claude = {
        "backend": "claude",
        "executable": "/synthetic/.local/bin/claude",
        "real_path": "/synthetic/.local/lib/node_modules/@anthropic-ai/claude-code/bin/claude",
        "version": outcome("claude 2.1.0"),
        "identity": outcome(
            json.dumps(
                {
                    "loggedIn": True,
                    "email": " synthetic@example.invalid ",
                    "subscriptionType": " Max ",
                    "authMethod": " oauth ",
                }
            )
        ),
        "writable": True,
        "latest": "2.2.0",
        "login": None,
    }
    hermes = {
        "backend": "hermes",
        "executable": "/synthetic/hermes/venv/bin/hermes",
        "real_path": "/synthetic/hermes/venv/bin/hermes",
        "version": outcome("hermes 1.2.3"),
        "identity": None,
        "writable": False,
        "latest": None,
        "login": None,
    }
    return [
        {
            **codex,
            "name": "codex-native-saved-login",
            "codex_catalog": {
                "models": [
                    {
                        "model_id": model,
                        "display_name": display,
                        "reasoning_effort_options": efforts,
                        "default_reasoning_effort": "medium",
                        "is_default": index == 0,
                    }
                    for index, (model, display, efforts) in enumerate(
                        [
                            ("gpt-6.1-sol", "Sol", ["medium", "high"]),
                            ("gpt-6-luna", "Luna", ["medium"]),
                        ]
                    )
                ],
                "reasoning_effort_options": ["medium", "high"],
                "default_model_id": "gpt-6.1-sol",
                "default_reasoning_effort": "medium",
                "complete": True,
            },
        },
        {
            **codex,
            "name": "codex-rejected-unavailable",
            "login": {**cast(dict[str, Any], codex["login"]), "rejected": True},
        },
        {
            **claude,
            "name": "claude-npm-aliases",
            "claude_catalog": {
                "models": [
                    {
                        "model_id": "opus",
                        "display_name": "Opus 5 (1M)",
                        "resolved_model_id": "claude-opus-5[1m]",
                        "reasoning_effort_options": ["high", "max"],
                    },
                    {
                        "model_id": "haiku",
                        "display_name": "Haiku 4.5",
                        "resolved_model_id": "claude-haiku-4-5-20251001",
                        "reasoning_effort_options": [],
                    },
                ],
                "reasoning_effort_options": [],
                "default_model_id": "claude-opus-5[1m]",
            },
        },
        {
            **claude,
            "name": "claude-manual-unavailable",
            "executable": "/opt/homebrew/bin/claude",
            "real_path": "/opt/homebrew/Cellar/claude-code/2/bin/claude",
            "writable": False,
            "version": outcome("unrecognized version"),
            "identity": outcome("invalid JSON"),
        },
        {
            **hermes,
            "name": "hermes-inventory",
            "hermes_catalog": {
                "schemaVersion": 1,
                "status": "runnable",
                "defaultModelId": "provider:small",
                "providers": [
                    {
                        "id": "provider",
                        "displayName": "Provider",
                        "models": [
                            {
                                "id": "provider:small",
                                "displayName": "Small",
                                "detail": "Configured model",
                            }
                        ],
                    }
                ],
            },
        },
        {
            **hermes,
            "name": "hermes-invalid-inventory",
            "hermes_catalog": {
                "schemaVersion": 1,
                "status": "runnable",
                "defaultModelId": "missing",
                "providers": [],
            },
        },
    ]


class RecordedEnvironment:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.commands: list[list[str]] = []
        self.catalog_calls: list[dict[str, Any]] = []
        self.registry_lookups: list[str] = []

    def executable_path(self, executable_name: str) -> str | None:
        return (
            cast(str, self.fixture["executable"])
            if executable_name == self.fixture["backend"]
            else None
        )

    def configured_executable_path(self, executable_name: str) -> str | None:
        return self.executable_path(executable_name)

    def real_path(self, path: str) -> str:
        return cast(str, self.fixture["real_path"]) if path == self.fixture["executable"] else path

    def user_local_npm_prefix(self) -> str:
        return "/synthetic/.local"

    def prefix_is_owned_and_writable(self, prefix: str) -> bool:
        return prefix == "/synthetic/.local" and bool(self.fixture["writable"])

    async def run(self, argv: Any, **options: Any) -> CommandOutcome:
        command = list(argv)
        if len(command) >= 2 and Path(command[1]).name == "hermes_model_catalog.py":
            self.catalog_calls.append({"provider": "hermes", "refresh": "--refresh" in command})
            return CommandOutcome(0, json.dumps(self.fixture["hermes_catalog"]), "")
        self.commands.append(command)
        if command == [self.fixture["executable"], "--version"]:
            return CommandOutcome(**self.fixture["version"])
        arguments = (
            ["auth", "status", "--json"]
            if self.fixture["backend"] == "claude"
            else ["login", "status"]
        )
        if (
            command == [self.fixture["executable"], *arguments]
            and self.fixture["identity"] is not None
        ):
            return CommandOutcome(**self.fixture["identity"])
        raise AssertionError("Unrecorded command")

    async def latest_released_version(self, package_name: str, **options: Any) -> str | None:
        self.registry_lookups.append(package_name)
        return cast(str | None, self.fixture["latest"])


async def python_answer(cases: list[dict[str, Any]]) -> dict[str, Any]:
    cards = []
    for fixture in cases:
        environment = RecordedEnvironment(fixture)

        async def codex_catalog(
            executable: str,
            fixture: dict[str, Any] = fixture,
            environment: RecordedEnvironment = environment,
        ) -> CodexModelCatalog:
            environment.catalog_calls.append({"provider": "codex", "executable": executable})
            if "codex_catalog" not in fixture:
                raise CodexModelCatalogUnavailable()
            raw = fixture["codex_catalog"]
            return CodexModelCatalog(
                **{**raw, "models": tuple(CodexModel(**model) for model in raw["models"])}
            )

        async def claude_catalog(
            executable: str,
            fixture: dict[str, Any] = fixture,
            environment: RecordedEnvironment = environment,
        ) -> ClaudeModelCatalog:
            environment.catalog_calls.append({"provider": "claude", "executable": executable})
            if "claude_catalog" not in fixture:
                raise ClaudeModelCatalogUnavailable()
            raw = fixture["claude_catalog"]
            return ClaudeModelCatalog(
                **{**raw, "models": tuple(ClaudeModel(**model) for model in raw["models"])}
            )

        raw_login = fixture["login"]
        login = None
        if raw_login is not None:
            stored = {key: value for key, value in raw_login.items() if key != "rejected"}
            stored["generation"] = CodexLoginGeneration(**stored["generation"])
            stored["access_token_expires_at"] = datetime.fromisoformat(
                stored["access_token_expires_at"]
            )
            login = CodexLoginStanding(StoredCodexLogin(**stored), raw_login["rejected"])
        card = await probe_backend(
            Backend(fixture["backend"]),
            environment,
            codex_model_catalog_probe=codex_catalog,
            claude_model_catalog_probe=claude_catalog,
            refresh=True,
            discover_update_advisory=True,
            codex_login=login,
            now=NOW,
        )
        cards.append(
            {
                "name": fixture["name"],
                "card": asdict(card),
                "commands": environment.commands,
                "catalog_calls": environment.catalog_calls,
                "registry_lookups": environment.registry_lookups,
            }
        )
    return {
        "cards": cards,
        "versions": [parse_version(value) for value in ["codex-cli v1.2.3 (synthetic)", "1.2.3.4"]],
        "ordering": [
            _version_is_newer(latest, installed)
            for latest, installed in [("1.2.10", "1.2.9"), ("1.2", "1.2.0"), ("preview", "beta")]
        ],
    }


def test_unit19_backend_probe(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    cases = fixtures()
    spec = tmp_path / "probe.json"
    spec.write_text(json.dumps(cases))
    with synthetic_home_environment(homes):
        expected = json.loads(json.dumps(asyncio.run(python_answer(cases))))
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
                    "const {run}=await import(pathToFileURL(process.argv[2]).href); "
                    "const spec=JSON.parse(readFileSync(process.argv[1],'utf8')); "
                    "process.stdout.write(JSON.stringify(await run(spec)));",
                    str(spec),
                    str(ROOT / "server/conversation/snapshot/testing/probe_comparison.ts"),
                ],
                cwd=ROOT,
                text=True,
            )
        )
    report = {
        "kind": "direct",
        "case": "unit19-backend-probe",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit19-backend-probe.json").write_text(json.dumps(report, indent=2) + "\n")
    assert len(expected["cards"][0]["card"]["available_models"]) == 2
    assert expected["cards"][0]["card"]["identity"]["account_label"] == "synthetic@example.invalid"
    assert expected["cards"][2]["card"]["update_advisory"]["update_available"]
    assert actual == expected
