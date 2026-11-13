"""Single-Mac ingress tests do not prove permanent handover or machine-bound routing.

Use existing ASGI ingress and real owner services. No socket or browser host starts.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import httpx
from fastapi import FastAPI

from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
from planner.environments.mac_updates import MacUpdateService
from planner.environments.update_attempts import FileUpdateAttemptStore
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.app_transport import build_executor_router
from planner.executor.credential_file import read_credential
from planner.executor.mac_http import MacHttpClient
from planner.executor.vps_transport import VpsExecutor


def test_automatic_handover_persists_secret_and_refuses_old_authority(tmp_path: Path) -> None:
    async def run() -> None:
        database = str(tmp_path / "home.db")
        with closing(connect(database)) as conn:
            create_schema(conn)
        identity = ExecutorIdentity(PROTOCOL_VERSION, "test", SPOOL_FORMAT_VERSION, str(uuid4()))
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
        headers = {"Tailscale-User-Login": "owner@test", "X-Panels-Executor-ID": "original"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://home.test"
        ) as http:
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 200
            client = MacHttpClient("https://home.test", "original", identity)

            async def request(
                loop: str, method: str, path: str, **kwargs: object
            ) -> tuple[int, bytes]:
                response = await http.request(
                    method, path, content=cast(bytes | None, kwargs.get("body")),
                    headers={**headers, **client.authentication_headers()},
                )
                return response.status_code, response.content

            client._json = request  # type: ignore[method-assign]
            path = tmp_path / "executor" / "machine-credential.json"
            # The first reply was lost before local persistence; bootstrap repeats it.
            lost = await http.post(
                "/api/executor/credential", headers=headers,
                json={"protocol_version": PROTOCOL_VERSION, "application_revision": "test",
                      "spool_format_version": SPOOL_FORMAT_VERSION,
                      "executor_incarnation": str(uuid4())},
            )
            assert lost.status_code == 200
            await client.ensure_credential(path)
            secret = read_credential(path)
            assert secret == lost.json()["credential"]
            assert secret
            assert client.authentication_headers()["X-Panels-Executor-Credential"] == secret
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 200
            assert (await http.get(
                "/api/executor/update", headers={**headers, **client.authentication_headers()}
            )).status_code == 200
            assert (await http.get("/api/executor/update", headers=headers)).status_code == 403
            # Retry loads its durable copy and never asks the one-time endpoint again.
            await client.ensure_credential(path)
            assert (await http.post(
                "/api/executor/credential", headers=headers,
                json={"protocol_version": PROTOCOL_VERSION, "application_revision": "test",
                      "spool_format_version": SPOOL_FORMAT_VERSION,
                      "executor_incarnation": str(uuid4())},
            )).status_code == 403
            await client.close()
        await endpoint.close()

    asyncio.run(run())


def test_operator_registration_and_replacement_refuse_worker_authority(tmp_path: Path) -> None:
    import pytest

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
