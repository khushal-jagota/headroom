"""Compose the Mac modules and keep their shared Link dialled."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from uuid import uuid4

import aiohttp

from planner.conversation.message_files import ConversationMessageFiles
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity, ProtocolRefused, parse_identity
from planner.executor.catalog_relay import MacCatalogRelay
from planner.executor.configuration import (
    RECONNECT_INTERVAL_SECONDS,
    ExecutorPaths,
    validate_server_url,
)
from planner.executor.diagnostics import ConnectionPhase, connection_diagnostics
from planner.executor.link import Link, create_client_session, dial_link
from planner.executor.mac_agent_gateway import AgentGateway
from planner.executor.mac_connection import MacConnection
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import (
    ChildCommand,
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
        self.connection = MacConnection(
            ExecutorIdentity(PROTOCOL_VERSION, revision, SPOOL_FORMAT_VERSION, str(uuid4()), 0)
        )
        self._stop = asyncio.Event()
        self._command_tasks: set[asyncio.Task[None]] = set()
        self.phase = ConnectionPhase.app_connect
        self.outbox = MacOutbox(paths.spool)
        self.catalog = MacCatalogRelay()
        self.runner = MacRunner(
            paths=paths,
            incarnation=self.identity.executor_incarnation,
            outbox=self.outbox,
            panels_server_url=self.server_url,
            message_files=ConversationMessageFiles(paths.message_files_anchor),
            connection=self.connection,
        )
        self.gateway = AgentGateway(
            runner=self.runner,
            outbox=self.outbox,
            connection=self.connection,
            request_exit=self._stop.set,
            settle_catalogs=self.catalog.settle,
        )

    @property
    def identity(self) -> ExecutorIdentity:
        return self.connection.identity

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
        current = self.connection.link
        if current is not None:
            with contextlib.suppress(ConnectionError, BrokenPipeError):
                await current.tell(
                    WireMessageKind.command_outcome,
                    as_object(outcome),
                    request_id=command.command_id,
                )

    def _require_handshake(self) -> None:
        if self.connection.link is None:
            raise ProtocolRefused("peer sent work before validated handshake")

    async def _catalog_request(self, link: Link, message: WireMessage) -> None:
        self.catalog.handle(message, link, admit=partial(self.connection.accepting, link))

    async def _withdraw(self, message: WireMessage) -> WireMessage:
        self._require_handshake()
        request = type_adapter(WithdrawCommands).validate_python(message.payload)
        return WireMessage(
            WireMessageKind.withdraw_result,
            message.request_id,
            as_object(WithdrawResult(self.runner.withdraw_commands(request))),
        )

    async def _start_command(self, link: Link, message: WireMessage) -> None:
        self._require_handshake()
        command = type_adapter(ChildCommand).validate_python(message.payload)
        task = asyncio.create_task(self._command(command, link))
        self._command_tasks.add(task)
        task.add_done_callback(self._command_tasks.discard)

    async def _reconnect_ask(self, message: WireMessage) -> WireMessage:
        self._require_handshake()
        ask = type_adapter(ReconnectAsk).validate_python(message.payload)
        await self.runner.flush_command_output(ask.command_ids)
        return WireMessage(
            WireMessageKind.reconnect_result,
            message.request_id,
            as_object(
                ReconnectResult(
                    self.identity, self.runner.ask(ask.command_ids), self.runner.children()
                )
            ),
        )

    async def _handshake(self, link: Link) -> None:
        self.phase = ConnectionPhase.hello
        response = await link.request(WireMessageKind.hello, as_object(self.connection.health()))
        if (
            response.kind is not WireMessageKind.hello_accepted
            or parse_identity(response.payload) != self.identity
        ):
            link.close()
            raise ProtocolRefused("handshake identity changed")
        self.connection.link = link
        self.phase = ConnectionPhase.reconnect_ask
        response = await link.request(
            WireMessageKind.reconciliation_ready, as_object(self.identity)
        )
        if response.payload != {"ready": True}:
            raise ProtocolRefused("invalid reconciliation acknowledgment")
        self.connection.lost_at_ms = None
        self.connection.state = ConnectionState.ready
        self.phase = ConnectionPhase.steady_state
        await self.outbox.send_pending(link)

    async def _serve(self) -> None:
        link: Link | None = None
        tasks: list[asyncio.Task[None] | asyncio.Task[bool]] = []
        async with contextlib.AsyncExitStack() as transport:
            try:
                link = await transport.enter_async_context(
                    dial_link(
                        self.server_url, self.executor_id, self.identity, self._session_factory
                    )
                )
                # Register every receiver before hello can admit work from the VPS.
                link.on(WireMessageKind.catalog_request, partial(self._catalog_request, link))
                link.on(WireMessageKind.withdraw, self._withdraw)
                link.on(WireMessageKind.command, partial(self._start_command, link))
                link.on(WireMessageKind.reconnect_ask, self._reconnect_ask)
                tasks = [
                    asyncio.create_task(link.closed()),
                    asyncio.create_task(self._handshake(link)),
                    asyncio.create_task(self._stop.wait()),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    await task
            finally:
                self.connection.lost()
                for task in tasks:
                    task.cancel()
                if link is not None:
                    link.close()
                await self.catalog.settle()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _connected(self) -> None:
        self.phase = ConnectionPhase.app_connect
        self.connection.identity = replace(
            self.identity, connection_generation=self.identity.connection_generation + 1
        )
        self.connection.state = ConnectionState.reconciling
        await self._serve()

    async def run(self) -> None:
        try:
            async with self.gateway.serve(self.paths.ipc_socket):
                while not self._stop.is_set():
                    try:
                        await self._connected()
                    except (
                        OSError,
                        ValueError,
                        ConnectionError,
                        asyncio.IncompleteReadError,
                        aiohttp.ClientError,
                    ) as error:
                        connection_diagnostics.report(error, phase=self.phase)
                        if not self._stop.is_set():
                            with contextlib.suppress(TimeoutError):
                                await asyncio.wait_for(
                                    self._stop.wait(), RECONNECT_INTERVAL_SECONDS
                                )
        finally:
            self.outbox.close()
