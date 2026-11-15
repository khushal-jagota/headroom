"""Single-Mac ingress tests do not prove permanent handover or machine-bound routing.

Use existing ASGI ingress and real owner services. No socket or browser host starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import aiohttp
import httpx
import pytest
from fastapi import FastAPI

from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import FileUpdateAttemptStore, UpdateConflict
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor import mac_updates, runtime_bootstrap
from planner.executor.app_transport import build_executor_router
from planner.executor.configuration import ExecutorPaths
from planner.executor.credential_file import read_credential, write_credential
from planner.executor.mac_http import MacHttpClient
from planner.executor.mac_presence import MacPresence
from planner.executor.mac_runner import MacRunner
from planner.executor.mac_updates import MacUpdateController
from planner.executor.runtime_bootstrap import atomic_record, read_record
from planner.executor.update_configuration import MAC_RUNTIME_READY_ENV
from planner.executor.update_contracts import MacRuntimeSelection, MacUpdateReport
from planner.executor.vps_transport import VpsExecutor
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID
from planner.machines.executor_credentials import executor_platform


def _handover_home(tmp_path: Path) -> tuple[FastAPI, VpsExecutor, str]:
    database = str(tmp_path / "home.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
    endpoint = VpsExecutor(database, RealClock())
    app = FastAPI()
    app.state.executor_id = "original"
    app.state.executor_endpoint = endpoint
    app.state.config = SimpleNamespace(db_path=database)
    app.state.mac_updates = MacUpdateService(
        tmp_path / "updates", FileUpdateAttemptStore(tmp_path / "attempt.json"),
        None, None, lambda: None,
    )
    app.include_router(build_executor_router())
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig("tailscale", "owner@test", "https://home.test"),
    )
    return app, endpoint, database


def _routed_client(http: httpx.AsyncClient, headers: dict[str, str]) -> MacHttpClient:
    identity = ExecutorIdentity(PROTOCOL_VERSION, "test", SPOOL_FORMAT_VERSION, str(uuid4()))
    client = MacHttpClient("https://home.test", "original", identity)

    async def request(loop: str, method: str, path: str, **kwargs: object) -> tuple[int, bytes]:
        response = await http.request(
            method, path, content=cast(bytes | None, kwargs.get("body")),
            headers={**headers, **client.authentication_headers()},
        )
        return response.status_code, response.content

    client._json = request  # type: ignore[method-assign]
    return client


def test_handover_closes_only_at_a_credentialed_close_request(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app, endpoint, database = _handover_home(tmp_path)
        updates = cast(MacUpdateService, app.state.mac_updates)
        accepted: list[str] = []

        def report(value: MacUpdateReport, health: object) -> None:
            if value.request_id != "trial":
                raise UpdateConflict("The Mac update report does not match its target.")
            accepted.append(value.phase)

        # The report's own target and presence rules are MacUpdateService's; this test
        # holds only what the route does with an accepted or refused report.
        reporting = SimpleNamespace(
            machine_id=ORIGINAL_MAC_MACHINE_ID, check=updates.check, report=report,
        )
        updates.for_machine = (  # type: ignore[method-assign]
            lambda machine_id: cast(MacUpdateService, reporting)
        )
        headers = {"Tailscale-User-Login": "owner@test", "X-Panels-Executor-ID": "original"}
        grant_body = {
            "protocol_version": PROTOCOL_VERSION, "application_revision": "test",
            "spool_format_version": SPOOL_FORMAT_VERSION, "executor_incarnation": str(uuid4()),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://home.test"
        ) as http:
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 200
            client = _routed_client(http, headers)
            path = tmp_path / "executor" / "machine-credential.json"
            # The first reply was lost before local persistence; bootstrap repeats it.
            lost = await http.post("/api/executor/credential", headers=headers, json=grant_body)
            assert lost.status_code == 200
            await client.ensure_credential(path)
            secret = read_credential(path)
            assert secret == lost.json()["credential"]
            assert secret
            credentialed = {**headers, **client.authentication_headers()}
            assert credentialed["X-Panels-Executor-Credential"] == secret
            # The pending grant goes only to the old authority: never to a request that
            # carries a credential, and never to another claimed executor.
            assert (await http.post(
                "/api/executor/credential", headers=credentialed, json=grant_body,
            )).status_code == 403
            assert (await http.post(
                "/api/executor/credential",
                headers={**headers, "X-Panels-Executor-ID": "other"}, json=grant_body,
            )).status_code == 403
            assert (await http.get("/api/executor/update", headers=credentialed)).status_code == 200
            assert client.authentication_headers()["X-Panels-Executor-Platform"] == sys.platform
            with closing(connect(database)) as conn:
                assert executor_platform(conn, ORIGINAL_MAC_MACHINE_ID) == sys.platform
            # The trial runtime is credentialed and leased, and even its accepted "current"
            # report leaves the old authority open: the supervisor has not kept it yet. If
            # it crashes or misses the supervisor's window, the old runtime still works,
            # and a restarted trial gets the same grant.
            current = {"request_id": "trial", "application_revision": "test", "phase": "current"}
            assert (await http.post(
                "/api/executor/update", headers=credentialed, json=current,
            )).json() == {"confirmed": True}
            assert accepted == ["current"]
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 200
            await client.ensure_credential(path)
            assert read_credential(path) == secret
            # Only a credentialed close request, sent once the supervisor keeps the trial,
            # closes it. The old authority cannot close itself.
            close = "/api/executor/credential/handover/close"
            assert (await http.post(close, headers=headers)).status_code == 403
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 200
            assert (await http.post(close, headers=credentialed)).json() == {"closed": True}
            assert (await http.post(close, headers=credentialed)).json() == {"closed": True}
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 403
            assert (await http.post(
                "/api/executor/credential", headers=headers, json=grant_body,
            )).status_code == 403
            # A later start keeps its saved credential once the home refuses.
            await client.ensure_credential(path)
            assert client.authentication_headers()["X-Panels-Executor-Credential"] == secret
            assert (await http.get(
                "/api/executor/update", headers={**headers, **client.authentication_headers()},
            )).status_code == 200
            await client.close()
        await endpoint.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("trial", "supervisor_poll_seconds"),
    [
        ("kept", 0.01),
        ("dies_before_marker", 0.01),
        # The supervisor polls once, at launch, and next at its deadline. The marker
        # lands between the two, as a "current" report near the deadline does.
        ("marker_after_last_poll", 0.3),
    ],
)
def test_old_authority_stays_open_until_the_real_supervisor_keeps_the_trial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trial: str,
    supervisor_poll_seconds: float,
) -> None:
    """The real supervise() runs stand-in runtimes. The real close loop asks the real home."""
    root = tmp_path / "executor" / "updates"
    root.mkdir(parents=True)
    runtime = tmp_path / "trial-runtime"
    runtime.mkdir()
    revision = "b" * 40
    selection = MacRuntimeSelection(
        "trial", revision, str(runtime), None, "a" * 40, str(uuid4()), 0, "c" * 64
    )
    calls: list[str | None] = []

    def launch(
        arguments: list[str],
        selected: str | None,
        launched_revision: str,
        *,
        ready: Path | None = None,
        handoff: Path | None = None,
    ) -> subprocess.Popen[bytes]:
        calls.append(selected)
        if selected is None and len(calls) == 1:
            # The old runtime stages the trial and asks for the swap.
            atomic_record(root / "pending.json", asdict(selection))
            program = "raise SystemExit(75)"
        elif selected is not None:
            assert ready is not None
            marker = json.dumps({"request_id": "trial", "application_revision": revision})
            write = (
                f"p = pathlib.Path({str(ready)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                f"p.write_text({marker!r})"
            )
            program = {
                "kept": f"import pathlib, time; {write}; time.sleep(0.5)",
                "dies_before_marker": "raise SystemExit(1)",
                "marker_after_last_poll": f"import pathlib, time; time.sleep(0.1); {write}; "
                "time.sleep(2)",
            }[trial]
        else:
            # The restored old runtime, which has only the old authority.
            program = "raise SystemExit(0)"
        return subprocess.Popen([sys.executable, "-c", program], start_new_session=True)

    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(root.parent))
    monkeypatch.setenv(MAC_RUNTIME_READY_ENV, str(root / "ready" / "trial.json"))
    monkeypatch.setattr(runtime_bootstrap, "_launch", launch)
    monkeypatch.setattr(runtime_bootstrap, "MAC_RUNTIME_READY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(runtime_bootstrap, "MAC_UPDATE_POLL_SECONDS", supervisor_poll_seconds)
    monkeypatch.setattr(mac_updates, "MAC_UPDATE_POLL_SECONDS", 0.01)
    trial_started = threading.Event()
    supervisor_done = threading.Event()
    outcome: dict[str, object] = {}

    async def home_and_trial_runtime() -> None:
        app, endpoint, _ = _handover_home(tmp_path)
        updates = cast(MacUpdateService, app.state.mac_updates)
        # The service's own target rules are proven elsewhere; here it accepts the report.
        reporting = SimpleNamespace(
            machine_id=ORIGINAL_MAC_MACHINE_ID, check=updates.check, report=lambda *_: None,
        )
        updates.for_machine = (  # type: ignore[method-assign]
            lambda machine_id: cast(MacUpdateService, reporting)
        )
        headers = {"Tailscale-User-Login": "owner@test", "X-Panels-Executor-ID": "original"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://home.test"
        ) as http:
            client = _routed_client(http, headers)
            await client.ensure_credential(tmp_path / "executor" / "machine-credential.json")
            credentialed = {**headers, **client.authentication_headers()}
            current = {"request_id": "trial", "application_revision": revision, "phase": "current"}
            assert (await http.post(
                "/api/executor/update", headers=credentialed, json=current,
            )).json() == {"confirmed": True}
            presence = MacPresence(
                ExecutorIdentity(PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()))
            )
            presence.accepted()
            controller = MacUpdateController(
                paths=cast(ExecutorPaths, SimpleNamespace(root=root.parent)),
                runner=cast(MacRunner, SimpleNamespace(planned_update_replay=False)),
                presence=presence,
                server_url="https://home.test",
                executor_id="original",
                session_factory=cast(Callable[[], aiohttp.ClientSession], contextlib.nullcontext),
                request_exit=lambda: None,
            )

            async def ask_home(
                session: object, method: str, path: str, payload: object = None
            ) -> object:
                response = await http.request(method, path, headers=credentialed)
                if response.status_code != 200:
                    raise ConnectionError("refused")
                return response.json()

            monkeypatch.setattr(controller, "_json", ask_home)
            # The home accepted this trial's "current" report, so it writes its marker.
            controller.confirmed_request = selection.request_id
            closing_task = asyncio.create_task(controller.close_handover_once_kept())
            trial_started.set()
            await asyncio.to_thread(supervisor_done.wait)
            # Long enough for many close-loop polls after the supervisor's decision.
            await asyncio.sleep(0.2)
            outcome["closed"] = closing_task.done() and closing_task.exception() is None
            closing_task.cancel()
            outcome["old_authority"] = (
                await http.get("/api/executor/update", headers=headers)
            ).status_code
            outcome["credential"] = (
                await http.get("/api/executor/update", headers=credentialed)
            ).status_code
            await client.close()
        await endpoint.close()

    observer = threading.Thread(target=asyncio.run, args=(home_and_trial_runtime(),))
    observer.start()
    try:
        assert trial_started.wait(10)
        returncode = runtime_bootstrap.supervise(
            ["executor", "run", "--application-revision=" + "a" * 40]
        )
    finally:
        supervisor_done.set()
        observer.join(10)
    assert returncode == 0
    assert outcome["credential"] == 200
    if trial == "kept":
        assert read_record(root / "selection.json") == asdict(selection)
        assert calls == [None, str(runtime)]
        assert outcome == {"closed": True, "old_authority": 403, "credential": 200}
    else:
        # The supervisor reverted, the trial never saw itself kept, and the restored
        # runtime still reaches the home with the old authority.
        failure = read_record(root / "failure.json")
        assert failure is not None and failure["phase"] == "reverted"
        assert read_record(root / "selection.json") is None
        assert calls == [None, str(runtime), None]
        assert outcome == {"closed": False, "old_authority": 200, "credential": 200}


def test_restored_home_replaces_a_stale_saved_credential_and_unreachable_home_keeps_it(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        path = tmp_path / "executor" / "machine-credential.json"
        # The Mac saved a credential that a home database restore no longer knows.
        write_credential(path, "s" * 43)
        app, endpoint, _ = _handover_home(tmp_path)
        headers = {"Tailscale-User-Login": "owner@test", "X-Panels-Executor-ID": "original"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://home.test"
        ) as http:
            stale = {**headers, "X-Panels-Executor-Credential": "s" * 43}
            assert (await http.get("/api/executor/update", headers=stale)).status_code == 403
            client = _routed_client(http, headers)
            await client.ensure_credential(path)
            fresh = read_credential(path)
            assert fresh is not None and fresh != "s" * 43
            assert (await http.get(
                "/api/executor/update", headers={**headers, **client.authentication_headers()},
            )).status_code == 200
            await client.close()
        await endpoint.close()

        async def unreachable(*args: object, **kwargs: object) -> tuple[int, bytes]:
            raise aiohttp.ClientConnectionError("home unreachable")

        identity = ExecutorIdentity(PROTOCOL_VERSION, "test", SPOOL_FORMAT_VERSION, str(uuid4()))
        offline = MacHttpClient("https://home.test", "original", identity)
        offline._json = unreachable  # type: ignore[method-assign]
        await offline.ensure_credential(path)
        assert offline.authentication_headers()["X-Panels-Executor-Credential"] == fresh
        assert read_credential(path) == fresh
        with pytest.raises(aiohttp.ClientConnectionError):
            await offline.ensure_credential(tmp_path / "empty" / "machine-credential.json")
        await offline.close()

    asyncio.run(run())


def test_operator_registration_and_replacement_refuse_worker_authority(tmp_path: Path) -> None:
    from planner.core.errors import PlannerError
    from planner.machines.credential_api import router

    async def run() -> None:
        database = str(tmp_path / "home.db")
        with closing(connect(database)) as conn:
            create_schema(conn)
        app = FastAPI()
        app.state.conn_factory = lambda: connect(database)
        app.include_router(router, prefix="/api")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_01234567"}
            with pytest.raises(PlannerError):
                await http.post("/api/machines", headers=worker, json={"name": "Forbidden"})
            registered = await http.post("/api/machines", json={"name": "Second Mac"})
            assert registered.status_code == 200
            grant = registered.json()
            path = f"/api/machines/{grant['machine_id']}/credential"
            with pytest.raises(PlannerError):
                await http.post(path, headers=worker)
            replaced = await http.post(path)
            assert replaced.status_code == 200
            assert replaced.json()["credential"] != grant["credential"]

    asyncio.run(run())
