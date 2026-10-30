"""Read-only Mac provider catalog relay, independent of run and write semantics."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from planner.conversation.contracts import (
    WorkspacePathPolicy,
)
from planner.conversation.model_resolution.contracts import (
    CatalogRequest,
    CatalogResponse,
    CatalogUnavailable,
    CorrectiveAction,
    ProviderCatalogObserver,
    ValidationReason,
)
from planner.core.task_settlement import wait_for_settlement
from planner.execution_identity import (
    ExecutorIdentity,
    parse_identity,
)
from planner.executor.configuration import (
    CATALOG_CANCEL_TIMEOUT_SECONDS,
    CATALOG_TIMEOUT_SECONDS,
    MAX_CATALOG_OBSERVATIONS,
)
from planner.executor.link import Link
from planner.executor.protocol import (
    WireMessage,
    WireMessageKind,
    as_object,
)
from planner.tickets.contracts import TicketLocale


class MacCatalogRelay:
    def __init__(self, server_url: str, observer: ProviderCatalogObserver | None = None) -> None:
        self.server_url = server_url
        self._catalog_observer = observer
        self._catalog_tasks: dict[str, tuple[CatalogRequest, Link, asyncio.Task[None]]] = {}

    def handle(self, message: WireMessage, link: Link, *, admit: Callable[[], bool]) -> None:
        # Metadata never enters the child registry or the durable journal.
        try:
            request = TypeAdapter(CatalogRequest).validate_json(
                json.dumps(message.payload), strict=True
            )
            context = message.payload["expected_context"]
            assert isinstance(context, dict)
            identity = parse_identity(context.get("executor_identity"))
        except (ValueError, TypeError, KeyError):
            return
        if (
            identity != link.identity
            or request.expected_context.executor_identity != identity
            or not admit()
        ):
            return
        workspace = request.expected_context.workspace
        if workspace.workspace_path_policy is WorkspacePathPolicy.native_absolute:
            if (
                workspace.workspace_home_relative is not None
                or not workspace.workspace_folder.is_absolute()
            ):
                return
        elif workspace.workspace_home_relative is not None:
            relative = Path(workspace.workspace_home_relative)
            if relative.is_absolute() or ".." in relative.parts:
                return
        existing = self._catalog_tasks.get(message.request_id)
        if message.kind is WireMessageKind.catalog_cancel:
            if existing is not None and existing[0] == request and existing[1] is link:
                # Repeated cancellation must not interrupt physical cleanup.
                if not existing[2].cancelling():
                    existing[2].cancel()
            return
        if existing is not None or not admit():
            return
        if len(self._catalog_tasks) >= MAX_CATALOG_OBSERVATIONS:
            # The caller's bounded read yields unresolved evidence. No queue can
            # reserve unbounded tasks while an earlier probe physically settles.
            return
        writer = link
        deadline = asyncio.get_running_loop().time() + CATALOG_TIMEOUT_SECONDS
        task = asyncio.create_task(
            self._catalog(message, request, identity, writer, deadline, admit)
        )
        self._catalog_tasks[message.request_id] = (request, writer, task)

        def settled(completed: asyncio.Task[None]) -> None:
            current = self._catalog_tasks.get(message.request_id)
            if current is not None and current[2] is completed:
                self._catalog_tasks.pop(message.request_id, None)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(settled)

    async def _catalog(
        self,
        message: WireMessage,
        request: CatalogRequest,
        identity: ExecutorIdentity,
        writer: Link,
        deadline: float,
        admit: Callable[[], bool],
    ) -> None:
        observer = self._catalog_observer
        if observer is None or (
            hasattr(observer, "executor_identity") and observer.executor_identity != identity
        ):
            from planner.conversation.model_resolution.observer import ProductionCatalogObserver

            observer = ProductionCatalogObserver(
                destination=TicketLocale.macos,
                panels_server_url=self.server_url,
                executor_identity=identity,
            )
            self._catalog_observer = observer
        owner = asyncio.current_task()
        assert owner is not None
        from planner.conversation.model_resolution.contracts import CatalogResult

        async def observe() -> CatalogResult:
            return await observer.observe(
                request.backend_key,
                workspace=request.expected_context.workspace,
                attempt_id=message.request_id,
                deadline=deadline,
            )

        work = asyncio.create_task(observe())
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    result = await asyncio.shield(work)
            except BaseException as error:
                work.cancel()
                await wait_for_settlement(work)
                if isinstance(error, asyncio.CancelledError):
                    raise
                result = CatalogUnavailable(
                    backend_key=request.backend_key,
                    destination=TicketLocale.macos,
                    attempted_context=None,
                    reason=ValidationReason.timeout
                    if isinstance(error, TimeoutError)
                    else ValidationReason.catalog_unavailable,
                    corrective_action=CorrectiveAction.refresh_catalog,
                    detail="The destination catalog observation did not complete.",
                    observed_at=int(time.time()),
                    retained_evidence=None,
                )
            # A catalog result has no authority after cancellation or generation
            # replacement. In particular, never write through self._send here.
            async with asyncio.timeout(CATALOG_CANCEL_TIMEOUT_SECONDS):
                await writer.tell(
                    WireMessageKind.catalog_response,
                    as_object(CatalogResponse(request.backend_key, identity, result)),
                    request_id=message.request_id,
                    admit=lambda: admit() and not owner.cancelling(),
                )
        except (ConnectionError, OSError, ValueError, TimeoutError):
            # A read-only failure does not disturb a shared execution connection.
            return
        finally:
            if not work.done():
                work.cancel()
            await wait_for_settlement(work)

    async def settle(self) -> None:
        tasks = tuple(task for _, _, task in self._catalog_tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        for task in tasks:
            await wait_for_settlement(task)
