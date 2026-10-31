"""Read-only provider catalog relays for the VPS and Mac peers."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from planner.conversation.contracts import (
    WorkspacePathPolicy,
)
from planner.conversation.model_resolution.contracts import (
    CatalogObservation,
    CatalogRequest,
    CatalogResponse,
    CatalogResult,
    CatalogUnavailable,
    CorrectiveAction,
    LaunchContext,
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
    ClockSample,
    JsonObject,
    WireMessage,
    WireMessageKind,
    as_object,
    encode_value,
    type_adapter,
)
from planner.tickets.contracts import TicketLocale

if TYPE_CHECKING:
    from planner.executor.vps_runs import MacRuns


class MacCatalogRelay:
    def __init__(self, server_url: str, observer: ProviderCatalogObserver | None = None) -> None:
        self.server_url = server_url
        self._catalog_observer = observer
        self._catalog_tasks: dict[str, tuple[CatalogRequest, Link, asyncio.Task[None]]] = {}

    def handle(self, message: WireMessage, link: Link, *, admit: Callable[[], bool]) -> None:
        # Metadata never enters the child registry or the durable journal.
        try:
            request = type_adapter(CatalogRequest).validate_json(
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


class VpsCatalogRelay:
    def __init__(self, runs: MacRuns) -> None:
        self.runs = runs
        self.clock_sample: ClockSample | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def identity(self) -> ExecutorIdentity | None:
        return self.runs.identity

    @property
    def link(self) -> Link | None:
        return self.runs.link

    @property
    def ready(self) -> bool:
        return self.runs.ready

    def owns_conversation(self, conversation_id: str) -> bool:
        return self.runs.owns_conversation(conversation_id)

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def observe_catalog(
        self,
        request: CatalogRequest,
        *,
        attempt_id: str,
        deadline: float | None = None,
    ) -> CatalogResult:
        """Read scoped metadata without child authority or transport timeout effects."""
        identity, writer = self.identity, self.link
        failure_reason = ValidationReason.catalog_unavailable
        if not attempt_id:
            raise ValueError("catalog attempt ID must be nonempty")
        if (
            identity is None
            or writer is None
            or not self.ready
            or request.expected_context.executor_identity != identity
        ):
            return self._catalog_failure(request, ValidationReason.offline)
        request_id = str(uuid4())
        expires = asyncio.get_running_loop().time() + CATALOG_TIMEOUT_SECONDS
        if deadline is not None:
            expires = min(expires, deadline)
        completed = False
        try:
            async with asyncio.timeout_at(expires):
                response = await writer.request(
                    WireMessageKind.catalog_request,
                    _object(request),
                    request_id=request_id,
                    timeout=max(0, expires - asyncio.get_running_loop().time()),
                    close_on_timeout=False,
                    admit=lambda: self.link is writer and self.identity == identity and self.ready,
                )
                if self.link is not writer or self.identity != identity or not self.ready:
                    raise ConnectionError("catalog generation changed")
                if response.kind is not WireMessageKind.catalog_response:
                    raise ValueError("unexpected catalog reply")
                self._catalog_raw_identities(response.payload)
                parsed = type_adapter(CatalogResponse).validate_json(
                    json.dumps(response.payload), strict=True
                )
                if parse_identity(response.payload.get("executor_identity")) != identity:
                    raise ValueError("catalog reply identity changed")
                failure_reason = ValidationReason.context_changed
                if (
                    parsed.backend_key != request.backend_key
                    or parsed.executor_identity != identity
                ):
                    raise ValueError("catalog reply backend changed")
                result = self._catalog_result(parsed.result, request, request_id, attempt_id)
                completed = True
                return result
        except TimeoutError:
            failure_reason = ValidationReason.timeout
        except (ConnectionError, OSError):
            failure_reason = ValidationReason.offline
        except (ValueError, TypeError, KeyError):
            pass
        finally:
            if not completed:
                # Cancellation is a bounded read-only frame on the original
                # writer. A lost frame leaves the native deadline in force.
                task = asyncio.create_task(
                    self._cancel_catalog(
                        request_id,
                        request,
                        identity,
                        writer,
                    )
                )
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        return self._catalog_failure(request, failure_reason)

    def _catalog_failure(
        self, request: CatalogRequest, reason: ValidationReason
    ) -> CatalogUnavailable:
        return CatalogUnavailable(
            backend_key=request.backend_key,
            destination=TicketLocale.macos,
            attempted_context=None,
            reason=reason,
            corrective_action=CorrectiveAction.reconnect_destination
            if reason is ValidationReason.offline
            else CorrectiveAction.refresh_catalog,
            detail="The destination catalog could not be verified.",
            observed_at=int(time.time()),
            retained_evidence=None,
        )

    @staticmethod
    def _catalog_raw_identities(payload: JsonObject) -> None:
        # Existing ExecutorIdentity predates extra="forbid". Validate the raw
        # dictionaries before a dataclass parser can discard an unknown field.
        parse_identity(payload.get("executor_identity"))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("catalog result must be an object")
        contexts: list[object] = []
        if result.get("kind") == "observation":
            evidence = result.get("evidence")
            if not isinstance(evidence, dict):
                raise ValueError("catalog evidence must be an object")
            contexts.append(evidence.get("context"))
        elif result.get("kind") == "unavailable":
            if result.get("attempted_context") is not None:
                contexts.append(result["attempted_context"])
            retained = result.get("retained_evidence")
            if retained is not None:
                if not isinstance(retained, dict):
                    raise ValueError("retained catalog evidence must be an object")
                contexts.append(retained.get("context"))
        for context in contexts:
            if not isinstance(context, dict):
                raise ValueError("catalog context must be an object")
            parse_identity(context.get("executor_identity"))
            generation = context.get("account_generation")
            if generation is not None:
                if (
                    not isinstance(generation, dict)
                    or set(generation) != {"account_id", "last_refresh"}
                    or any(type(value) is not str or not value for value in generation.values())
                ):
                    raise ValueError("invalid catalog account generation fields")

    def _catalog_context(self, context: LaunchContext, request: CatalogRequest) -> None:
        if (
            context.backend_key != request.backend_key
            or context.destination is not TicketLocale.macos
            or context.executor_identity != request.expected_context.executor_identity
            or context.workspace != request.expected_context.workspace
            or not context.localized_workspace_folder.is_absolute()
        ):
            raise ValueError("catalog context differs from request")
        # Nested identity uses the same strict field and version validation as hello.
        if parse_identity(_object(context.executor_identity)) != self.identity:
            raise ValueError("catalog nested identity differs from connection")
        workspace = context.workspace
        from planner.conversation.contracts import WorkspacePathPolicy

        if workspace.workspace_path_policy is WorkspacePathPolicy.native_absolute:
            if workspace.workspace_home_relative is not None:
                raise ValueError("native catalog workspace cannot use home mapping")
        elif workspace.workspace_home_relative is not None:
            relative = Path(workspace.workspace_home_relative)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe catalog workspace mapping")

    def _catalog_result(
        self, result: CatalogResult, request: CatalogRequest, request_id: str, attempt_id: str
    ) -> CatalogResult:
        if isinstance(result, CatalogObservation):
            self._catalog_context(result.evidence.context, request)
            if result.evidence.attempt_id != request_id:
                raise ValueError("catalog attempt differs from request")
            uncertainty: float | None = None
            sample = self.clock_sample
            if sample is not None and result.evidence.clock_uncertainty_seconds is not None:
                lower = sample.mac_sent_at_ms - sample.vps_received_at_ms
                upper = sample.mac_received_at_ms - sample.vps_sent_at_ms
                if (
                    sample.vps_received_at_ms >= sample.vps_sent_at_ms
                    and sample.mac_sent_at_ms >= sample.mac_received_at_ms
                    and lower <= upper
                ):
                    uncertainty = (
                        result.evidence.clock_uncertainty_seconds
                        + max(abs(lower), abs(upper)) / 1000
                        + 1.0
                    )
            return replace(
                result,
                evidence=replace(
                    result.evidence,
                    attempt_id=attempt_id,
                    clock_uncertainty_seconds=uncertainty,
                ),
            )
        if (
            result.backend_key != request.backend_key
            or result.destination is not TicketLocale.macos
        ):
            raise ValueError("catalog failure differs from request")
        if result.attempted_context is not None:
            self._catalog_context(result.attempted_context, request)
        if result.retained_evidence is not None:
            self._catalog_context(result.retained_evidence.context, request)
        return result

    async def _cancel_catalog(
        self,
        request_id: str,
        request: CatalogRequest,
        identity: ExecutorIdentity,
        writer: Link,
    ) -> None:
        try:
            async with asyncio.timeout(CATALOG_CANCEL_TIMEOUT_SECONDS):
                await writer.tell(
                    WireMessageKind.catalog_cancel,
                    _object(request),
                    request_id=request_id,
                    admit=lambda: self.link is writer and self.identity == identity and self.ready,
                )
        except (ConnectionError, OSError, ValueError, TimeoutError):
            pass

    async def _measure_clock(self) -> None:
        identity, writer = self.identity, self.link
        if writer is None:
            return
        sent = time.time_ns() // 1_000_000
        response = await writer.request(
            WireMessageKind.health, {"clock_probe": {"vps_sent_at_ms": sent}}
        )
        if self.identity != identity or self.link is not writer:
            return
        self.clock_sample = ClockSample(
            sent,
            int(cast(int, response.payload["mac_received_at_ms"])),
            int(cast(int, response.payload["mac_sent_at_ms"])),
            time.time_ns() // 1_000_000,
        )


def _object(value: object) -> JsonObject:
    encoded = encode_value(value)
    if not isinstance(encoded, dict):
        raise ValueError("expected object")
    return encoded
