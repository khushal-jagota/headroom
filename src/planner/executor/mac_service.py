"""Compose the Mac modules and keep their shared Link dialled."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import replace
from uuid import uuid4

import aiohttp

from planner.conversation.message_files import ConversationMessageFiles
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import (
    ExecutorIdentity,
    ProtocolRefused,
)
from planner.executor.catalog_relay import MacCatalogRelay
from planner.executor.configuration import (
    RECONNECT_INTERVAL_SECONDS,
    ExecutorPaths,
    validate_server_url,
)
from planner.executor.diagnostics import connection_diagnostics
from planner.executor.link import Link, create_client_session, dial_link
from planner.executor.mac_agent_gateway import AgentGateway
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner, now_ms
from planner.executor.protocol import (
    ChildCommand,
    ConnectionFailureOrigin,
    ConnectionPhase,
    ConnectionState,
    ReconnectAsk,
    ReconnectResult,
    WireMessage,
    WireMessageKind,
    WithdrawCommands,
    WithdrawResult,
    as_object,
    type_adapter,
)


class MacService:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        revision: str,
        executor_id: str,
        panels_server_url: str,
        session_factory: Callable[[], aiohttp.ClientSession] = create_client_session,
    ) -> None:
        self.paths = paths
        self.server_url = validate_server_url(panels_server_url)
        self.executor_id = executor_id
        self._session_factory = session_factory
        self.identity = ExecutorIdentity(
            PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()), 0
        )
        self.outbox = MacOutbox(paths.spool)
        self.catalog = MacCatalogRelay()
        self.runner = MacRunner(
            paths=paths,
            incarnation=self.identity.executor_incarnation,
            outbox=self.outbox,
            panels_server_url=self.server_url,
            message_files=ConversationMessageFiles(paths.root / "artifacts" / "unused-db-path"),
            connection=lambda: self.gateway.connection(),
            ready=lambda: self.gateway.state is ConnectionState.ready,
        )
        self.gateway = AgentGateway(
            runner=self.runner,
            outbox=self.outbox,
            identity=lambda: self.identity,
            settle_catalogs=self.catalog.settle,
        )

    async def _command(self, command: ChildCommand, link: Link) -> None:
        try:
            outcome = await self.runner.run(command)
        except (OSError, ValueError, sqlite3.Error):
            # Required output may not have reached disk. Reconnect retries that
            # publication before answering the ask; it never repeats the provider call.
            link.close()
            return
        # A command belongs to the retained native registry, not the connection
        # that first carried it. Publish its stable outcome on the admitted link;
        # if hello has not completed, the reconnect ask will read that outcome.
        current = self.gateway._link
        if current is not None and self.gateway._handshake_accepted:
            with contextlib.suppress(ConnectionError, BrokenPipeError):
                await current.tell(
                    WireMessageKind.command_outcome,
                    as_object(outcome),
                    request_id=command.command_id,
                )

    async def _connected(self, command_tasks: set[asyncio.Task[None]] | None = None) -> None:
        if command_tasks is None:
            command_tasks = set()
        phase = ConnectionPhase.app_connect

        async def observe[T](awaitable: Awaitable[T], origin: ConnectionFailureOrigin) -> T:
            try:
                return await awaitable
            except Exception as error:
                connection_diagnostics.report(error, phase=phase, origin=origin)
                raise

        self.identity = replace(
            self.identity, connection_generation=self.identity.connection_generation + 1
        )
        self.gateway.state = ConnectionState.reconciling
        self.gateway._handshake_accepted = False
        identity = self.identity
        reader_task: asyncio.Task[None] | None = None
        session_task: asyncio.Task[None] | None = None
        stop_task: asyncio.Task[bool] | None = None
        try:
            async with dial_link(
                self.server_url, self.executor_id, identity, self._session_factory
            ) as link:
                self.gateway._link = link

                async def on_message(message: WireMessage) -> WireMessage | None:
                    if message.kind in (WireMessageKind.catalog_request,):
                        self.catalog.handle(
                            message, link, admit=lambda: self.gateway.accepting(link)
                        )
                        return None
                    if not self.gateway._handshake_accepted and message.kind in (
                        WireMessageKind.command,
                        WireMessageKind.reconnect_ask,
                        WireMessageKind.withdraw,
                    ):
                        raise ProtocolRefused("peer sent work before validated handshake")
                    if message.kind is WireMessageKind.withdraw:
                        request = type_adapter(WithdrawCommands).validate_python(message.payload)
                        return WireMessage(
                            WireMessageKind.withdraw_result,
                            message.request_id,
                            as_object(WithdrawResult(self.runner.withdraw_commands(request))),
                        )
                    if message.kind is WireMessageKind.command:
                        command = type_adapter(ChildCommand).validate_python(message.payload)
                        task = asyncio.create_task(self._command(command, link))
                        command_tasks.add(task)
                        task.add_done_callback(command_tasks.discard)
                    elif message.kind is WireMessageKind.reconnect_ask:
                        ask = type_adapter(ReconnectAsk).validate_python(message.payload)
                        await self.runner.flush_command_output(ask.command_ids)
                        return WireMessage(
                            WireMessageKind.reconnect_result,
                            message.request_id,
                            as_object(
                                ReconnectResult(
                                    self.identity,
                                    self.runner.ask(ask.command_ids),
                                    self.runner.children(),
                                )
                            ),
                        )
                    return None

                for kind in (
                    WireMessageKind.catalog_request,
                    WireMessageKind.health,
                    WireMessageKind.withdraw,
                    WireMessageKind.command,
                    WireMessageKind.reconnect_ask,
                ):
                    link.on(kind, on_message)
                reader_task = asyncio.create_task(
                    observe(link.closed(), ConnectionFailureOrigin.reader)
                )

                async def session() -> None:
                    nonlocal phase
                    phase = ConnectionPhase.hello
                    await link.request(WireMessageKind.hello, as_object(self.gateway.health()))
                    self.gateway._handshake_accepted = True
                    phase = ConnectionPhase.transport_open
                    opened = await link.request(
                        WireMessageKind.transport_open, {"identity": as_object(identity)}
                    )
                    if opened.kind is not WireMessageKind.transport_opened:
                        raise ValueError("file generation refused")
                    phase = ConnectionPhase.reconnect_ask
                    response = await link.request(
                        WireMessageKind.reconciliation_ready, as_object(identity)
                    )
                    if response.payload != {"ready": True}:
                        raise ProtocolRefused("invalid reconciliation acknowledgment")
                    self.gateway.lost_at_ms = None
                    self.gateway.state = ConnectionState.ready
                    phase = ConnectionPhase.steady_state
                    await self.outbox.send_pending(link)

                session_task = asyncio.create_task(
                    observe(session(), ConnectionFailureOrigin.session)
                )
                stop_task = asyncio.create_task(self.gateway._stop.wait())
                try:
                    done, _ = await asyncio.wait(
                        (reader_task, session_task, stop_task), return_when=asyncio.FIRST_COMPLETED
                    )
                    for done_task in done:
                        await done_task
                finally:
                    self.gateway._link = None
                    self.gateway._handshake_accepted = False
                    self.gateway.state = ConnectionState.disconnected
                    for task in (reader_task, session_task, stop_task):
                        task.cancel()
                    link.close()
                    await self.catalog.settle()
                    await asyncio.gather(
                        reader_task, session_task, stop_task, return_exceptions=True
                    )
        except Exception as error:
            connection_diagnostics.report(
                error, phase=phase, origin=ConnectionFailureOrigin.session
            )
            raise
        finally:
            self.gateway._handshake_accepted = False
            self.gateway.state = ConnectionState.disconnected
            self.gateway._link = None
            if self.gateway.lost_at_ms is None:
                self.gateway.lost_at_ms = now_ms()

    async def run(self) -> None:
        command_tasks: set[asyncio.Task[None]] = set()
        try:
            async with self.gateway.serve(self.paths.ipc_socket):
                while not self.gateway._stop.is_set():
                    try:
                        await self._connected(command_tasks)
                    except (
                        OSError,
                        ValueError,
                        ConnectionError,
                        asyncio.IncompleteReadError,
                        aiohttp.ClientError,
                    ) as error:
                        connection_diagnostics.report(error)
                        if not self.gateway._stop.is_set():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(
                                    self.gateway._stop.wait(), RECONNECT_INTERVAL_SECONDS
                                )
        finally:
            self.outbox.close()
