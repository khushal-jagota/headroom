"""Pair the real backend routes with recording services through in-process HTTP.

The shared Python host replaces its snapshot service with a browser-only fixture
that cannot run updates. These route cases instead inject the complete real
snapshot and usage services, with recorded external actions. No new listener,
browser host, login, CLI update, or provider request exists here.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry
from tests.e2e.conversation_signals_server import NoInstalledBackends
from tests.e2e.ticket_model_controls import install_ticket_model_controls
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)

from planner.conversation import api
from planner.conversation.backend_state import (
    BackendStateStore,
    model_refusal,
    offered_model_ids,
    save_machine_model_list,
)
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageService,
    BackendUsageWindow,
    BackendUsageWindowKind,
)
from planner.conversation.backends.codex_app_server.login_session import (
    CodexSignIn,
    SignInState,
    UnknownSignInSession,
)
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.snapshot import (
    BackendIdentity,
    BackendIdentityStatus,
    BackendInstallMethod,
    BackendModel,
    BackendSnapshot,
    BackendSnapshotService,
    BackendUpdateAdvisory,
    CommandOutcome,
)
from planner.core.clock import TestClock as FakeClock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.execution_identity import TicketLocale
from planner.managers.data import create_manager
from planner.projects.data import create_project
from planner.tickets.data import create_ticket

ROOT = Path(__file__).resolve().parents[3]
NOW = "2026-11-12T12:00:00Z"


def model_admission_evidence(db_path: Path, cards: list[dict[str, Any]]) -> dict[str, Any]:
    # One retained sequence proves cross-machine retirement and exact alias admission.
    with closing(connect(str(db_path))) as conn:
        initial = {
            key.value: list(offered_model_ids(conn, TicketLocale.vps, key) or ()) for key in Backend
        }
        for model in ("retired-model", "mac-only"):
            conn.execute("INSERT INTO backend_model_enablement VALUES ('codex',?,0)", (model,))
        codex = card(next(value for value in cards if value["backend_key"] == "codex"))
        mac = replace(codex, available_models=(BackendModel("mac-only"),), models_complete=False)
        save_machine_model_list(conn, TicketLocale.macos, mac)
        save_machine_model_list(conn, TicketLocale.vps, codex)
        before = [
            row[0]
            for row in conn.execute(
                "SELECT model_id FROM backend_model_enablement "
                "WHERE model_id IN ('retired-model','mac-only') ORDER BY model_id"
            )
        ]
        save_machine_model_list(conn, TicketLocale.macos, replace(mac, models_complete=True))
        after = [
            row[0]
            for row in conn.execute(
                "SELECT model_id FROM backend_model_enablement "
                "WHERE model_id IN ('retired-model','mac-only') ORDER BY model_id"
            )
        ]
        aliases = {
            model: model_refusal(conn, Backend.claude, model, TicketLocale.vps)
            for model in ("opus", "opus[1m]", "claude-opus-5[1m]", "sonnet")
        }
        mac_refusal = model_refusal(conn, Backend.codex, "gpt-6.1-sol", TicketLocale.macos)
        conn.commit()
    return {
        "initial": initial,
        "before": before,
        "after": after,
        "aliases": aliases,
        "mac_refusal": mac_refusal,
    }


def fixture_cards() -> list[dict[str, Any]]:
    cards = []
    for backend, model, display in (
        ("hermes", "provider:small", "Small"),
        ("codex", "gpt-6.1-sol", "Strasse"),
        ("claude", "claude-opus-5", "Opus"),
    ):
        cards.append(
            {
                "backend_key": backend,
                "installed": True,
                "executable_path": "/synthetic/" + backend,
                "version": "1.0.0",
                "identity": (
                    None
                    if backend == "hermes"
                    else {
                        "status": "authenticated",
                        "account_label": "Synthetic account",
                        "detail": None,
                        "login_command": backend + " login",
                    }
                ),
                "available_models": [
                    {
                        "model_id": model,
                        "display_name": display,
                        "detail": "Synthetic catalog entry",
                        "reasoning_effort_options": (
                            [] if backend == "hermes" else ["medium", "high"]
                        ),
                        "selected_variant": None,
                        "version": [],
                        "resolved_model_id": model,
                    }
                ],
                "reasoning_effort_options": ([] if backend == "hermes" else ["medium", "high"]),
                "default_model_id": model,
                "default_reasoning_effort": None if backend == "hermes" else "medium",
                "update_advisory": (
                    {
                        "install_method": "native",
                        "update_command": ["synthetic-cli", "update"],
                        "latest_version": "2.0.0",
                        "update_available": True,
                        "detail": "Version 2.0.0 is available.",
                    }
                    if backend == "codex"
                    else None
                ),
                "diagnoses": [],
                "models_complete": True,
            }
        )
    return cards


def card(value: dict[str, Any]) -> BackendSnapshot:
    raw = dict(value)
    raw["backend_key"] = Backend(raw["backend_key"])
    raw["available_models"] = tuple(BackendModel(**model) for model in raw["available_models"])
    raw["reasoning_effort_options"] = tuple(raw["reasoning_effort_options"])
    raw["diagnoses"] = tuple(raw["diagnoses"])
    if raw["identity"] is not None:
        raw["identity"] = BackendIdentity(
            **{
                **raw["identity"],
                "status": BackendIdentityStatus(raw["identity"]["status"]),
            }
        )
    if raw["update_advisory"] is not None:
        advisory = raw["update_advisory"]
        raw["update_advisory"] = BackendUpdateAdvisory(
            **{
                **advisory,
                "install_method": BackendInstallMethod(advisory["install_method"]),
                "update_command": tuple(advisory["update_command"]),
            }
        )
    return BackendSnapshot(**raw)


class RecordedEnvironment(NoInstalledBackends):
    def __init__(self) -> None:
        self.version = "1.0.0"
        self.commands: list[list[str]] = []

    async def run(self, argv: Any, **options: Any) -> CommandOutcome:
        assert list(argv) == ["synthetic-cli", "update"]
        self.commands.append(list(argv))
        self.version = "2.0.0"
        return CommandOutcome(0, "Recorded update", "")


class RecordedSignIn:
    def __init__(self, value: dict[str, Any]) -> None:
        self.pending = CodexSignIn(
            **{
                **value,
                "state": SignInState(value["state"]),
                "started_at": datetime.fromisoformat(value["started_at"]),
                "deadline_at": datetime.fromisoformat(value["deadline_at"]),
            }
        )
        self.current: CodexSignIn | None = None
        self.calls: list[str] = []

    def read(self) -> CodexSignIn | None:
        return self.current

    async def start(self) -> CodexSignIn:
        self.calls.append("start")
        self.current = self.pending
        return self.current

    async def cancel(self, session_id: str) -> CodexSignIn:
        self.calls.append("cancel:" + session_id)
        if self.current is None or self.current.session_id != session_id:
            raise UnknownSignInSession()
        self.current = replace(
            self.current,
            state=SignInState.ended,
            verification_url=None,
            user_code=None,
            detail="Recorded cancellation",
        )
        return self.current


class RecordedUsage:
    def __init__(self, value: dict[str, Any]) -> None:
        self.result = BackendUsageResult(
            Backend(value["backend_key"]),
            BackendUsageOutcome(value["outcome"]),
            value["detail"],
            datetime.fromisoformat(value["observed_at"]),
            tuple(
                BackendUsageWindow(
                    BackendUsageWindowKind(window["kind"]),
                    window["used_percent"],
                    datetime.fromisoformat(window["resets_at"]),
                    window["model_scope"],
                )
                for window in value["windows"]
            ),
        )

    async def refresh(self) -> BackendUsageResult:
        return self.result


def test_unit19_backend_route_answers_and_rows(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    now = datetime.fromisoformat(NOW)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        project = create_project(conn, name="Backend proof", now=int(now.timestamp()))
        ticket = create_ticket(
            conn,
            title="Backend scope",
            project_id=project.id,
            brief="Synthetic",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
            now=int(now.timestamp()),
            title_max_chars=200,
        )
        manager = create_manager(
            conn, title="Backend caller", project_id=project.id, clock=FakeClock(now)
        )
        conn.commit()
    requests: list[dict[str, Any]] = [
        {"method": "GET", "path": "/api/conversation/backends"},
        {"method": "GET", "path": "/api/conversation/backends?refresh=true"},
        {
            "method": "PUT",
            "path": "/api/conversation/backends/codex/models/gpt-6.1-sol/enablement",
            "json": {"enabled": False},
        },
        {"method": "GET", "path": "/api/conversation/backends"},
        {
            "method": "POST",
            "path": "/api/conversation/backends",
            "json": {"ticket_id": ticket.id},
        },
        {"method": "POST", "path": "/api/conversation/backends/refresh"},
        {"method": "POST", "path": "/api/conversation/backends/codex/update"},
        {"method": "GET", "path": "/api/conversation/backends/codex/sign-in"},
        {"method": "POST", "path": "/api/conversation/backends/codex/sign-in"},
        {"method": "POST", "path": "/api/conversation/backends/codex/sign-in"},
        {"method": "DELETE", "path": "/api/conversation/backends/codex/sign-in/stale"},
        {
            "method": "DELETE",
            "path": "/api/conversation/backends/codex/sign-in/recorded",
        },
        {"method": "GET", "path": "/api/conversation/backends/codex/sign-in"},
        {"method": "POST", "path": "/api/conversation/backends/claude/sign-in"},
        {
            "method": "PUT",
            "path": "/api/conversation/backends/codex/models/missing/enablement",
            "json": {"enabled": True},
        },
        {"method": "GET", "path": "/api/conversation/backends/unknown/sign-in"},
    ]
    for headers in (
        {"x-plan-actor": "worker", "x-plan-ticket-id": ticket.id},
        {"x-plan-actor": "manager", "x-plan-manager-id": manager.id},
        {"x-plan-actor": "chief"},
    ):
        requests.append(
            {
                "method": "POST",
                "path": "/api/conversation/backends/codex/update",
                "headers": headers,
            }
        )
    requests.extend(
        [
            {
                "method": "POST",
                "path": "/api/conversation/backends?refresh=invalid",
                "json": {"ticket_id": "missing"},
            },
            {"method": "POST", "path": "/api/test/ticket-model-catalog", "json": {}},
            {"method": "GET", "path": "/api/conversation/backends"},
            {
                "method": "POST",
                "path": "/api/test/ticket-model-catalog",
                "json": {"credentials_unavailable": True},
            },
            {
                "method": "POST",
                "path": "/api/conversation/backends",
                "json": {"ticket_id": ticket.id},
            },
            {
                "method": "POST",
                "path": "/api/test/ticket-model-catalog",
                "json": {"credentials_unavailable": False},
            },
            {"method": "GET", "path": "/api/conversation/backends"},
        ]
    )
    requests.append(
        {
            "method": "POST",
            "path": "/api/conversation/backends",
            "json": {"ticket_id": ticket.id},
            "headers": {"x-plan-actor": "worker"},
        }
    )
    usage: list[dict[str, Any]] = [
        {
            "backend_key": backend,
            "outcome": "succeeded",
            "detail": None,
            "observed_at": NOW,
            "windows": [
                {
                    "kind": "five_hour",
                    "used_percent": 25,
                    "resets_at": "2026-11-13T12:00:00Z",
                    "model_scope": None,
                },
                {
                    "kind": "seven_day",
                    "used_percent": 40,
                    "resets_at": "2026-11-19T12:00:00Z",
                    "model_scope": scope,
                },
            ],
        }
        for backend, scope in (("codex", "STRASSE"), ("claude", "Opus"))
    ]
    pending = {
        "session_id": "recorded",
        "state": "pending",
        "verification_url": "https://login.invalid/device",
        "user_code": "TEST-CODE",
        "started_at": "2026-11-12T12:00:00.123456+00:00",
        "deadline_at": "2026-11-12T12:10:00Z",
        "detail": None,
        "account_label": None,
    }
    paths = [tmp_path / name / "data.sqlite" for name in ("python", "typescript")]
    for path in paths:
        path.parent.mkdir()
        with (
            closing(sqlite3.connect(seed)) as source,
            closing(sqlite3.connect(path)) as destination,
        ):
            source.backup(destination)
    environment = RecordedEnvironment()
    sign_in = RecordedSignIn(pending)
    cards = fixture_cards()

    async def probe(key: Backend, machine: Any, **options: Any) -> BackendSnapshot:
        result = card(next(value for value in cards if value["backend_key"] == key))
        return replace(result, version=environment.version) if key == Backend.codex else result

    with (
        synthetic_home_environment(homes),
        patch("planner.conversation.snapshot.probe_backend", probe),
        patch(
            "planner.conversation.snapshot.SubprocessBackendProbeEnvironment",
            NoInstalledBackends,
        ),
    ):
        config = load_config(
            None,
            env={
                "PLAN_DB_PATH": str(paths[0]),
                "PLAN_TEST_MODE": "1",
                "PLAN_DISPATCH_ENABLED": "false",
                "PLAN_LOGS_DIR": str(tmp_path / "logs"),
                "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            },
        )
        registry = ScriptedBackendRegistry()
        app = create_app(
            config,
            FakeClock(now),
            lambda: connect(str(paths[0])),
            backend_child_factories_for_test={key: registry.make_child for key in Backend},
        )
        runtime = SimpleNamespace(
            database_path=str(paths[0]),
            backend_snapshots=BackendSnapshotService(
                environment,
                now=lambda: now,
                keep_complete_model_list=BackendStateStore(str(paths[0])).keep_vps_model_list,
            ),
            backend_state=BackendStateStore(str(paths[0])),
            backend_usage=BackendUsageService(
                {Backend(value["backend_key"]): RecordedUsage(value) for value in usage}
            ),
            codex_sign_in=sign_in,
            system=SimpleNamespace(machine_catalog=None),
        )
        install_ticket_model_controls(app)

        def route_runtime() -> Any:
            runtime.backend_snapshots = app.state.conversation.backend_snapshots
            return runtime

        app.dependency_overrides[api._runtime] = route_runtime
        answers = []
        with TestClient(app) as client:
            object.__setattr__(
                app.state.conversation, "backend_snapshots", runtime.backend_snapshots
            )
            for operation in requests:
                response = client.request(
                    operation["method"],
                    operation["path"],
                    json=operation.get("json"),
                    headers=operation.get("headers"),
                )
                answers.append({"status": response.status_code, "body": response.json()})
        asyncio.run(registry.close())
    admission = model_admission_evidence(paths[0], cards)
    assert admission["initial"]["codex"] == ["gpt-6.1-sol"]
    assert admission["initial"]["claude"] == ["claude-opus-5"]
    assert admission["before"] == ["mac-only", "retired-model"]
    assert admission["after"] == ["mac-only"]
    assert all(
        admission["aliases"][model] is None for model in ("opus", "opus[1m]", "claude-opus-5[1m]")
    )
    assert admission["aliases"]["sonnet"] is not None
    with closing(sqlite3.connect(paths[0])) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in (
                "backend_model_enablement",
                "backend_usage_snapshots",
                "backend_usage_windows",
            )
        }
    expected = {
        "admission": admission,
        "answers": answers,
        "tables": tables,
        "commands": environment.commands,
        "sign_in_calls": sign_in.calls,
    }
    assert answers[8]["body"]["sign_in"]["started_at"] == pending["started_at"]
    assert answers[8]["body"]["sign_in"]["deadline_at"] == "2026-11-12T12:10:00+00:00"
    assert [entry["status"] for entry in answers] == [200] * 10 + [
        409,
        200,
        200,
        404,
        404,
        422,
        400,
        400,
        400,
    ] + [422] + [200] * 6 + [400]
    assert answers[-1]["body"]["error"]["code"] == "agent_forbidden"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "db_path": str(paths[1]),
                "now": NOW,
                "cards": cards,
                "usage": usage,
                "sign_in": pending,
                "requests": requests,
            }
        )
    )
    with synthetic_home_environment(homes):
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; "
                    "import {pathToFileURL} from 'node:url'; "
                    "const {run}=await import(pathToFileURL(process.argv[2]).href); "
                    "const spec=JSON.parse(readFileSync(process.argv[1],'utf8')); "
                    "process.stdout.write(JSON.stringify(await run(spec)));",
                    str(spec),
                    str(ROOT / "server/conversation/snapshot/testing/http_comparison.ts"),
                ],
                cwd=ROOT,
                text=True,
            )
        )
    report = {
        "kind": "direct-http",
        "case": "unit19-backend-routes",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "comparison_rule": "Keep JSON fields; use native model case matching and numbers.",
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit19-backend-routes.json").write_text(json.dumps(report, indent=2) + "\n")
    # Native case-insensitive model matching replaces Python's sharp-s expansion.
    # Numeric JSON spelling can differ, while booleans and strings stay distinct.
    compared = [
        json.dumps(
            json.loads(
                json.dumps(side),
                parse_float=lambda token: (
                    int(number) if (number := float(token)).is_integer() else number
                ),
            ),
            sort_keys=True,
        )
        for side in (expected, actual)
    ]
    assert compared[0] == compared[1]
