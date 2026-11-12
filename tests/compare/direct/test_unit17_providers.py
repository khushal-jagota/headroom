"""Fresh Python provider parity with one shared scripted executable and synthetic homes."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from planner.conversation import backend_usage
from planner.conversation.backend_state import DurableCodexLoginRejection
from planner.conversation.backend_usage import CodexUsageAdapter
from planner.conversation.backends.codex_app_server.login import (
    RememberedCodexLoginRejection,
    read_stored_codex_login,
)
from planner.conversation.backends.codex_app_server.login_session import (
    CodexSignIn,
    CodexSignInService,
    SignInBounds,
    UnknownSignInSession,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    probe_codex_model_catalog,
)
from planner.conversation.snapshot import read_codex_login_standing
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]
INSTANT = datetime.fromisoformat("2026-11-12T12:34:56.123456+00:00")


def plain(value: Any) -> Any:
    def scalar(item: Any) -> Any:
        if isinstance(item, datetime):
            return item.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return asdict(item)

    return json.loads(json.dumps(value, default=scalar))


def sign_in_view(value: Any) -> dict[str, Any]:
    result = asdict(value)
    result["started_at"] = value.started_at.isoformat()
    result["deadline_at"] = value.deadline_at.isoformat()
    return result


@contextmanager
def owned_launch_boundary(executable: str, children: list[Any]) -> Any:
    original = asyncio.create_subprocess_exec

    async def guarded(*arguments: Any, **keywords: Any) -> Any:
        if list(arguments) not in (
            [executable, "--version"],
            [executable, "app-server"],
        ):
            raise AssertionError(f"Unexpected provider child: {arguments}")
        child = await original(*arguments, **keywords)
        children.append(child)
        return child

    with patch.object(asyncio, "create_subprocess_exec", guarded):
        yield


async def python_exercise(spec: dict[str, Any], children: list[Any]) -> dict[str, Any]:
    home = Path(os.environ["CODEX_HOME"])
    memory = RememberedCodexLoginRejection()
    login_path = home / "auth.json"
    control = Path(spec["control"])
    trace = Path(spec["trace"])

    def mode(name: str) -> None:
        os.environ.update(
            UNIT17_PROVIDER_MODE=name,
            UNIT17_TRACE=str(trace),
            UNIT17_PROVIDER_CONTROL=str(control),
        )
        control.unlink(missing_ok=True)
        login_path.write_text(json.dumps(spec["login"]))

    def joined() -> bool:
        return all(child.returncode is not None for child in children)

    def saved(rejection: Any = memory) -> dict[str, Any]:
        standing = read_codex_login_standing(rejection)
        stored = standing.stored
        result: dict[str, Any] = plain(
            {
                "present": stored.present,
                "readable": stored.readable,
                "account_label": stored.account_label,
                "access_token_expires_at": stored.access_token_expires_at,
                "generation": standing.generation,
                "rejected": standing.rejected,
                "expired": stored.access_token_has_expired(now=INSTANT),
            }
        )
        return result

    async def wait(condition: Any, name: str) -> None:
        deadline = time.monotonic() + 5
        while not condition():
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out: {name}")
            await asyncio.sleep(0.005)

    models: dict[str, Any] = {}
    for name in ("models-full", "models-partial"):
        mode(name)
        catalog = await probe_codex_model_catalog(spec["executable"])
        models[name] = {"catalog": plain(catalog), "joined": joined()}
    mode("saved")
    standing = [saved()]
    memory.remember(read_stored_codex_login().generation)
    standing.append(saved())
    login_path.write_text(
        json.dumps({**spec["login"], "last_refresh": "refreshed-generation"})
    )
    standing.append(saved())
    sign_ins: list[dict[str, Any]] = []
    for name in (
        "signin-cancel",
        "signin-deadline",
        "signin-shutdown",
        "signin-complete",
        "signin-race",
        "signin-unconfirmed",
        "signin-child-success",
    ):
        mode(name)

        rejection: Any = memory
        case_clock = INSTANT
        if name == "signin-shutdown":
            case_clock = INSTANT.replace(microsecond=0)
            rejection = DurableCodexLoginRejection(spec["database"])

            rejection.remember(read_stored_codex_login().generation)

        async def argv() -> tuple[str, ...]:
            return str(spec["executable"]), "app-server"

        def current_time(value: datetime = case_clock) -> datetime:
            return value

        service = CodexSignInService(
            resolve_argv=argv,
            remembered_login_rejection=rejection,
            now=current_time,
            bounds=(
                SignInBounds()
                if name == "signin-shutdown"
                else SignInBounds(
                    child_startup_seconds=3,
                    session_seconds=0.06 if name == "signin-deadline" else 2,
                    cancel_seconds=0.5,
                )
            ),
        )
        try:
            if name == "signin-cancel":
                pending, concurrent = await asyncio.gather(
                    service.start(), service.start()
                )
                assert concurrent.session_id == pending.session_id
                with pytest.raises(UnknownSignInSession):
                    await service.cancel("stale-session")
            else:
                pending = await service.start()
            terminal: CodexSignIn | None
            if name in ("signin-cancel", "signin-race"):
                terminal = await service.cancel(pending.session_id)
            elif name == "signin-shutdown":
                await service.shutdown()
                terminal = service.read()
            else:
                if name != "signin-deadline":
                    control.write_text("complete")

                def finished(selected_service: CodexSignInService = service) -> bool:
                    current = selected_service.read()
                    assert current is not None
                    return current.state != "pending"

                await wait(finished, name)
                terminal = service.read()
            assert terminal is not None
            sign_ins.append(
                {
                    "name": name,
                    "pending": sign_in_view(pending),
                    "terminal": sign_in_view(terminal),
                    "joined": joined(),
                    "login": saved(rejection),
                }
            )
        finally:
            await service.shutdown()

    class UsageEnvironment:
        def executable_path(self, name: str) -> str:
            assert name == "codex"
            return str(spec["executable"])

    usage: list[dict[str, Any]] = []
    for name in (
        "usage-float",
        "usage-rejected",
        "usage-timeout",
        "usage-cancel",
    ):
        mode(name)
        adapter = CodexUsageAdapter(
            environment=UsageEnvironment(), now=lambda: INSTANT.replace(microsecond=0)
        )
        if name == "usage-cancel":
            operation = asyncio.create_task(adapter.refresh())
            try:
                await wait(
                    lambda selected_name=name: any(
                        frame.get("mode") == selected_name
                        and frame.get("method") == "account/rateLimits/read"
                        for frame in read_trace(trace)
                    ),
                    "usage cancellation native request",
                )
                operation.cancel()
                canceled = False
                try:
                    await operation
                except asyncio.CancelledError:
                    canceled = True
                usage.append({"name": name, "canceled": canceled, "joined": joined()})
            finally:
                if not operation.done():
                    operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        else:
            bound = 0.3 if name == "usage-timeout" else 30
            with patch.object(
                backend_usage, "CODEX_USAGE_REFRESH_TIMEOUT_SECONDS", bound
            ):
                result = await adapter.refresh()
            usage.append({"name": name, "result": plain(result), "joined": joined()})
    return {
        "models": models,
        "saved": standing,
        "sign_in": sign_ins,
        "usage": usage,
        "trace": read_trace(trace),
        "children": read_trace(Path(f"{trace}.children")),
    }


def read_trace(path: Path) -> list[dict[str, Any]]:
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def normalize(result: dict[str, Any]) -> dict[str, Any]:
    selected = {
        key: value for key, value in result.items() if key not in ("children", "raw")
    }
    sessions = []
    for session in result["sign_in"]:
        assert session["terminal"]["session_id"] == session["pending"]["session_id"]
        identity = f"session:{session['name']}"
        sessions.append(
            {
                **session,
                "pending": {**session["pending"], "session_id": identity},
                "terminal": {**session["terminal"], "session_id": identity},
            }
        )
    return {**selected, "sign_in": sessions}


def test_unit17_provider_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Prove four provider bindings, with bounded fake-process lifetime on each path."""
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    for name in ("codex", "claude", "hermes"):
        (home / name).mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_DATA_HOME": str(home / "data"),
        "TMPDIR": str(tmp_path),
    }

    def token(claims: dict[str, Any]) -> str:
        payload = (
            base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        )
        return f"unsigned.{payload}.synthetic"

    spec: dict[str, Any] = {
        "executable": str(tmp_path / "scripted_codex.ts"),
        "trace": str(tmp_path / "provider.jsonl"),
        "control": str(tmp_path / "provider.control"),
        "database": str(tmp_path / "provider.sqlite"),
        "login": {
            "last_refresh": "old-generation",
            "tokens": {
                "account_id": "synthetic-account",
                "access_token": token({"exp": 2100000000}),
                "id_token": token({"email": "owner@example.invalid"}),
            },
        },
    }
    shutil.copy2(
        ROOT / "server/conversation/testing/codex/scripted_app_server.ts",
        spec["executable"],
    )
    Path(spec["executable"]).chmod(0o700)
    children: list[Any] = []
    with (
        patch.dict(os.environ, environment, clear=True),
        owned_launch_boundary(spec["executable"], children),
    ):
        connection = connect(spec["database"])
        try:
            create_schema(connection)
            connection.commit()
            with sqlite3.connect(tmp_path / "typescript-provider.sqlite") as target:
                connection.backup(target)
        finally:
            connection.close()
        python = asyncio.run(
            asyncio.wait_for(python_exercise(spec, children), timeout=45)
        )
    for path in (
        Path(spec["trace"]),
        Path(f"{spec['trace']}.children"),
        Path(spec["control"]),
    ):
        path.unlink(missing_ok=True)
    spec["database"] = str(tmp_path / "typescript-provider.sqlite")
    input_path = tmp_path / "provider-input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
            "const {run_provider_parity}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await run_provider_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/codex/provider_parity.ts"),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    typescript = json.loads(completed.stdout)
    expected, actual = normalize(python), normalize(typescript)
    report = {
        "python": python,
        "typescript": typescript,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination) / "unit-17-providers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
    assert '"usedPercent":25.0' in typescript["raw"]["usage-float"]
    assert '"usedPercent":25,' in typescript["raw"]["usage-float"]
    for result in (python, typescript):
        assert sorted(
            row["pid"] for row in result["children"] if row["kind"] == "launch"
        ) == sorted(row["pid"] for row in result["children"] if row["kind"] == "exit")
