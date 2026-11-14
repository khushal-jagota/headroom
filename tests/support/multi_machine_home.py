"""Private connected proof host; no provider calls or deployment state.

The home runs in its own process so CPU excludes all executor and proxy work.
Only the test client can reach the ephemeral loopback listener and TLS proxy.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

import aiohttp
import uvicorn
from fastapi import FastAPI
from tests.support.executor_https import HttpsProxy, isolated_https_proxy


def clean_environment(root: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLAN_")}
    env.update(
        PLAN_TEST_MODE="1",
        CODEX_HOME=str(root / "codex"),
        PLAN_WORKER_PROCESS_ROOT=str(root / "workers"),
    )
    return env


async def run_home(root: Path, ready: Path) -> None:
    from planner.core.runtime_connections import reuse_connections

    # This proof owns its application lifespan directly, bypassing core.server.
    with reuse_connections(str(root / "home.sqlite")):
        await _run_home(root, ready)


async def _run_home(root: Path, ready: Path) -> None:
    from tests.integration.test_executor_end_to_end import (
        create_mac_ticket,
    )

    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem
    from planner.core.clock import RealClock
    from planner.core.db import ChangeSignallingConnection, connect
    from planner.core.runtime_connections import runtime_connection
    from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
    from planner.executor.app_transport import build_executor_router
    from planner.executor.registry import VpsExecutorRegistry
    from planner.executor.vps_presence import no_update_check

    counts: Counter[str] = Counter()
    # Distinguish logical checkouts from costly physical SQLite opens. Patch only
    # this private proof subprocess, including already-imported connect aliases.
    original_physical_connect = sqlite3.connect

    def physical_connect(*args: Any, **kwargs: Any) -> Any:
        database = args[0] if args else kwargs.get("database")
        if str(database) == str(root / "home.sqlite"):
            counts["physical_database_opens"] += 1
        return original_physical_connect(*args, **kwargs)

    sqlite3.connect = physical_connect
    original_checkout = connect

    def checkout(*args: Any, **kwargs: Any) -> Any:
        counts["fresh_connection_api_calls"] += 1
        return original_checkout(*args, **kwargs)

    def runtime_checkout(*args: Any, **kwargs: Any) -> Any:
        counts["runtime_connection_loans"] += 1
        return runtime_connection(*args, **kwargs)

    for module in tuple(sys.modules.values()):
        if module is not None and getattr(module, "connect", None) is original_checkout:
            vars(module)["connect"] = checkout
        if (
            module is not None
            and getattr(module, "runtime_connection", None) is runtime_connection
        ):
            vars(module)["runtime_connection"] = runtime_checkout
    # Count successful mutating commits and SQLite row changes, including triggers.
    # Hook the existing connection's final commit primitive; no runtime telemetry.
    original = ChangeSignallingConnection._sqlite_commit

    def commit(conn: Any) -> None:
        baseline = conn._rows_changed_at_transaction_start
        changes = 0 if baseline is None else conn.total_changes - baseline
        original(conn)
        if changes:
            counts["write_transactions"] += 1
            counts["row_changes"] += changes

    ChangeSignallingConnection._sqlite_commit = commit  # type: ignore[method-assign, assignment]

    original_sql = ChangeSignallingConnection._sqlite_execute

    def execute(conn: Any, cursor: Any, sql: str, parameters: Any) -> Any:
        baseline = conn._rows_changed_at_transaction_start
        had_transaction = conn.in_transaction
        changes = 0 if baseline is None else conn.total_changes - baseline
        try:
            result = original_sql(conn, cursor, sql, parameters)
        except Exception as error:
            if "database is locked" in str(error).lower():
                counts["database_locked_errors"] += 1
            raise
        # Explicit SQL COMMIT and outermost RELEASE bypass _sqlite_commit.
        if (
            had_transaction
            and not conn.in_transaction
            and changes
            and sql.lstrip().lower().startswith(("commit", "end", "release"))
        ):
            counts["write_transactions"] += 1
            counts["row_changes"] += changes
        return result

    ChangeSignallingConnection._sqlite_execute = execute  # type: ignore[method-assign, assignment]

    class Locks(logging.Handler):
        def emit(self, record: Any) -> None:
            if "database is locked" in record.getMessage().lower():
                counts["logged_database_locked_errors"] += 1

    logging.getLogger().addHandler(Locks())

    db = str(root / "home.sqlite")
    registry = VpsExecutorRegistry(db, RealClock())
    with contextlib.closing(connect(db)) as conn:
        from planner.worker_types.configuration import load_worker_runtime_definitions

        load_worker_runtime_definitions(conn)
        registry.load(conn)
    system = SqliteProcessConversationSystem(
        store=ConversationStore(db),
        backend_child_factories={key: registry for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(db),
    )
    system.configure_machine_catalog(registry)
    system.start_admission.configure_destinations(registry.is_present)
    app = FastAPI()
    app.state.db_path = db
    app.state.executor_id = None
    app.state.executor_registry = registry
    app.include_router(build_executor_router())
    app.add_middleware(
        TrustedIngressMiddleware,
        config=TrustedIngressConfig("tailscale", "owner@example.com", "https://localhost"),
    )
    registry.configure(system, app, update_check=lambda _: no_update_check())
    await registry.start()

    @app.post("/proof/start/{machine_id}/{conversation}")
    async def start(machine_id: str, conversation: str) -> dict[str, str]:
        ticket = create_mac_ticket(db, root, title=conversation)
        with contextlib.closing(connect(db)) as conn:
            conn.execute("UPDATE tickets SET machine_id=? WHERE id=?", (machine_id, ticket))
        from planner.conversation.contracts import (
            ConversationRoleMaterials,
            ConversationStartRequest,
            WorkspacePathPolicy,
        )
        from planner.conversation.message_content import text_message_content
        from planner.conversation.start_admission.contracts import StartRequest
        from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
        from planner.tickets import data

        await system.start_conversation(
            ConversationStartRequest(
                conversation_id=conversation,
                model="scripted",
                backend_key=ConversationBackendKey.codex,
                workspace_folder=root,
                workspace_path_policy=WorkspacePathPolicy.absolute,
                role_materials=ConversationRoleMaterials(
                    "A scripted worker.", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket))
                ),
            )
        )
        with contextlib.closing(connect(db)) as conn:
            data.write_ticket_conversation_start(
                conn,
                ticket,
                conversation_id=conversation,
                backend="codex",
                model="scripted",
                reasoning_effort=None,
                now=2,
            )
        identity = f"proof:{conversation}"
        system.start_admission.try_start(StartRequest(identity, "worker", machine_id))
        result = await system.send(
            conversation,
            text_message_content("Start"),
            sender_label="Test",
            sender=CHIEF_PRINCIPAL,
            recipient=Principal(PrincipalKind.ticket, ticket),
            sender_message_id=identity,
        )
        return {"ticket_id": ticket, "delivery": type(result).__name__}

    @app.get("/proof/state")
    async def state() -> dict[str, Any]:
        with contextlib.closing(connect(db)) as conn:
            return {
                "presence": {
                    str(r[0]): str(r[1])
                    for r in conn.execute(
                        "SELECT machine_id,executor_incarnation FROM mac_executor_presence"
                    )
                },
                "open_commands": conn.execute(
                    "SELECT count(*) FROM executor_commands WHERE state IN ('waiting','delivered')"
                ).fetchone()[0],
                "ended": {
                    str(r[0]): int(r[1])
                    for r in conn.execute(
                        "SELECT conversation_id,count(*) FROM conversation_events "
                        "WHERE kind='turn_ended' GROUP BY conversation_id"
                    )
                },
                "receipts": conn.execute("SELECT count(*) FROM mac_inbox_positions").fetchone()[0],
            }

    @app.get("/proof/metrics")
    async def metrics() -> dict[str, Any]:
        return {
            "home_pid": os.getpid(),
            "cpu_seconds": time.process_time(),
            "wall_monotonic": time.monotonic(),
            **counts,
        }

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    port_file = root / "listener-port"
    listener.bind(("127.0.0.1", int(port_file.read_text()) if port_file.exists() else 0))
    port_file.write_text(str(listener.getsockname()[1]))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        while not server.started:
            if task.done():
                await task
            await asyncio.sleep(0.01)
        ready.write_text(
            json.dumps(
                {"upstream": f"http://127.0.0.1:{listener.getsockname()[1]}", "pid": os.getpid()}
            )
        )
        await task
    finally:
        await registry.close()
        for conversation in system._conversations.values():
            conversation.child = None
        await system.shutdown()
        listener.close()


class Home:
    def __init__(self, root: Path, machines: int) -> None:
        self.root, self.count = root, machines
        self.grants: list[Any] = []
        self.process: subprocess.Popen[bytes] | None = None
        self.stack = contextlib.ExitStack()
        self.proxy: HttpsProxy
        self.session: aiohttp.ClientSession

    async def __aenter__(self) -> Home:
        from planner.core.db import connect, create_schema
        from planner.machines.executor_credentials import register_machine

        self.root.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(connect(str(self.root / "home.sqlite"))) as conn:
            create_schema(conn)
            self.grants = [register_machine(conn, f"Extra {n}") for n in range(self.count)]
        try:
            await self.start()
        except BaseException:
            await self.__aexit__()
            raise
        return self

    async def start(self) -> None:
        await self.start_process()
        upstream = json.loads((self.root / "ready.json").read_text())["upstream"]
        self.proxy = self.stack.enter_context(isolated_https_proxy(self.root / "tls", upstream))
        self.session = self.proxy.session_factory()

    async def stop_process(self) -> None:
        if self.process is not None:
            # Model a home crash. The handle identifies only this owned subprocess.
            ready = json.loads((self.root / "ready.json").read_text())
            os.kill(int(ready.get("pid", self.process.pid)), signal.SIGKILL)
            await asyncio.to_thread(self.process.wait, 5)
            self.process = None

    async def start_process(self) -> None:
        ready = self.root / "ready.json"
        ready.unlink(missing_ok=True)
        self.log = self.stack.enter_context((self.root / "home.log").open("ab"))
        command = [
            sys.executable,
            "-m",
            "tests.support.multi_machine_home",
            "--root",
            str(self.root),
            "--ready",
            str(ready),
        ]
        if os.environ.get("MULTI_HOME_PROFILE"):
            command = [
                str(shutil.which("py-spy")),
                "record",
                "--nonblocking",
                "--rate",
                "99",
                "--format",
                "speedscope",
                "--output",
                str(self.root / "profile.json"),
                "--",
                *command,
            ]
        self.process = subprocess.Popen(
            command, env=clean_environment(self.root), stdout=self.log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 20
        while not ready.exists():
            if self.process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError((self.root / "home.log").read_text())
            await asyncio.sleep(0.02)

    async def request(self, path: str, *, post: bool = False) -> dict[str, Any]:
        async with self.session.request(
            "POST" if post else "GET", self.proxy.origin + path
        ) as response:
            text = await response.text()
            assert response.status == 200, (
                response.status,
                text,
                (self.root / "home.log").read_text(),
            )
            return cast(dict[str, Any], json.loads(text))

    async def __aexit__(self, *args: Any) -> None:
        if hasattr(self, "session"):
            await self.session.close()
        await self.stop_process()
        self.stack.close()


class Executor:
    def __init__(self, root: Path, proxy: HttpsProxy, grant: Any) -> None:
        import ssl

        from tests.integration.test_executor_end_to_end import (
            ScriptedNativeChild,
            native_factory_for,
        )
        from tests.support.executor_catalog import ScriptedBackendSnapshots

        from planner.executor.configuration import ExecutorPaths
        from planner.executor.credential_file import credential_path, write_credential
        from planner.executor.mac_service import MacService

        class CliNativeChild(ScriptedNativeChild):
            async def start(self, resolved_start: Any, **kwargs: Any) -> None:
                self.environment = dict(
                    resolved_start.role_materials.identity_environment_variables
                )
                self.environment["PLAN_CONVERSATION_ID"] = resolved_start.conversation_id
                await super().start(resolved_start, **kwargs)

        self.native = CliNativeChild()
        self.mac = MacService(
            paths=ExecutorPaths(root),
            revision="a" * 40,
            executor_id=grant.machine_id,
            panels_server_url=proxy.origin,
            ssl_context=ssl.create_default_context(cafile=str(proxy.ca_path)),
        )
        write_credential(credential_path(root), grant.credential)
        self.submitted: dict[str, Any] = {}
        self.receipted: dict[str, Any] = {}
        add = self.mac.outbox.add
        acknowledge = self.mac.outbox.acknowledge

        def observed_add(*args: Any, **kwargs: Any) -> Any:
            envelope = add(*args, **kwargs)
            self.submitted[envelope.submission_id] = envelope
            return envelope

        def observed_acknowledge(receipt: Any) -> None:
            self.receipted[receipt.submission_id] = receipt
            acknowledge(receipt)

        self.mac.outbox.add = observed_add  # type: ignore[method-assign]
        self.mac.outbox.acknowledge = observed_acknowledge  # type: ignore[method-assign]
        observer = ScriptedBackendSnapshots(lambda: self.mac.identity)
        observer.model = "scripted"
        self.mac.catalog.snapshots = observer
        self.mac.runner.factory = native_factory_for(self.native)
        self.lease_bytes: list[int] = []
        post = self.mac.client.lease

        async def lease(request: Any) -> Any:
            from planner.executor.protocol import encode_bounded

            reply = await post(request)
            self.lease_bytes.append(len(encode_bounded(reply)))
            return reply

        self.mac.client.lease = lease  # type: ignore[method-assign]
        self.task: asyncio.Task[None] | None = None

    async def send_message(self, message: str) -> str:
        environment = clean_environment(self.mac.paths.root)
        environment.update(self.native.environment)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "planner", "send-message", "--owner", "--message", message,
            env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        return stdout.decode()

    async def __aenter__(self) -> Executor:
        self.task = asyncio.create_task(self.mac.run())
        await wait_for(lambda: self.mac.presence.leased(), "executor lease")
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.mac._stop.set()
        assert self.task is not None
        try:
            await asyncio.wait_for(self.task, 8)
        except TimeoutError:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def wait_for(condition: Any, detail: str, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(detail)
        await asyncio.sleep(0.02)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run_home(args.root, args.ready))
