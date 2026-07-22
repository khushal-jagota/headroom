"""Single-writer prompt delivery, FIFO, cancellation, and compaction broker."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from acp.schema import (
    CancelNotification,
    DeniedOutcome,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
)

from .configuration import (
    ACP_ACTIVE_PROMPT_CANCEL_TIMEOUT_SECONDS,
    ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
    ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS,
)
from .contracts import (
    ContextCompaction,
    ContextCompactionTrigger,
    ConversationActivityState,
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationSessionBinding,
    ProgrammaticPrompt,
    QueuedPrompt,
    TurnDeliveryChoice,
    TurnDeliveryReceipt,
)
from .permission_broker import ConversationPermissionBroker
from .reverse_services.terminal import ScopedAcpTerminalService
from .runtime_event_publisher import ConversationRuntimeEventPublisher
from .runtime_ports import (
    CompactionCaptureTransition,
    CompactionTransitionToken,
    ConversationCompactionCaptureDeadlineExpired,
    ConversationCompactionCaptureFailed,
    ConversationCompactionTransitionPort,
    ConversationEmployeeRuntimePort,
    ConversationRequestedCancelRecoveryTransitionPort,
    ConversationRuntimeGenerationFatal,
    ConversationRuntimeHandle,
    ConversationRuntimeLease,
    ConversationWaitDeadlineExpired,
    RequestedCancelRecoveryTransitionToken,
)
from .wire_contracts import HumanEcho, ProtocolUpdateRejectedPayload

logger = logging.getLogger(__name__)


class ConversationTurnBrokerError(RuntimeError):
    pass


class ConversationTurnBrokerShutdownError(ConversationTurnBrokerError):
    def __init__(self, unfinished_employee_ids: tuple[str, ...]) -> None:
        super().__init__(
            "conversation turn broker shutdown exceeded its deadline: "
            + ", ".join(unfinished_employee_ids)
        )
        self.unfinished_employee_ids = unfinished_employee_ids


class _ConversationReverseServiceCleanupError(RuntimeError):
    pass


type _CancellationCause = Literal[
    "user", "send_now", "new_conversation", "shutdown", "child_failure"
]

type TrackedTurnStatus = Literal["complete", "interrupted", "errored"]
type TrackedTurnFailureProvenance = Literal["backend", "conversation"]


def _backend_failure_reason(
    error: BaseException | None, provider_reason: object = None
) -> str:
    if (
        isinstance(provider_reason, str)
        and provider_reason
        and provider_reason == provider_reason.strip()
    ):
        return provider_reason
    if error is not None and (exception_reason := str(error).strip()):
        return exception_reason
    return "Employee connection failed"


@dataclass(frozen=True, slots=True)
class TrackedTurnResult:
    status: TrackedTurnStatus
    response: PromptResponse | None = None
    error: str | None = None
    failure_provenance: TrackedTurnFailureProvenance | None = None


@dataclass(frozen=True, slots=True)
class TrackedTurnHandle:
    employee_id: str
    acp_session_id: str
    binding_generation: int
    child_generation: int
    record_identity: object
    client_message_id: str
    prompt_epoch: int
    completion: asyncio.Future[TrackedTurnResult]


@dataclass(frozen=True, slots=True)
class ConversationTurnAttachState:
    phase: Literal[
        "idle", "running", "cancelling", "capture-finalizing", "failed", "closed"
    ]
    queued_prompts: tuple[QueuedPrompt, ...]


type TrackedTurnHook = Callable[[TrackedTurnHandle], None]


@dataclass(frozen=True, slots=True)
class PromptIngressBarrierResult:
    notifications: tuple[SessionNotification, ...] = ()
    rejection_reason: str | None = None


type PromptStartedHook = Callable[[ConversationRuntimeHandle, int], None]
type PromptSettlementBarrier = Callable[
    [ConversationRuntimeHandle, int], Awaitable[PromptIngressBarrierResult]
]


@dataclass(slots=True)
class _ActivePrompt:
    epoch: int
    client_message_id: str
    choice: TurnDeliveryChoice
    prompt: PromptRequest
    lease: ConversationRuntimeLease
    task: asyncio.Task[PromptResponse]
    cancellation_cause: _CancellationCause | None = None
    successor: _QueuedSubmission | None = None
    interrupted_published: bool = False
    cancel_task: asyncio.Task[None] | None = None
    timeout_task: asyncio.Task[None] | None = None
    permission_cancel_task: asyncio.Task[None] | None = None
    origin: Literal["browser", "worker"] = "browser"
    tracked_handle: TrackedTurnHandle | None = None
    tracked_terminal_hook: TrackedTurnHook | None = None
    requested_cancel_transition_token: RequestedCancelRecoveryTransitionToken | None = None
    requested_cancel_deadline: float | None = None
    close_deadline: float | None = None


@dataclass(frozen=True, slots=True)
class _QueuedSubmission:
    client_message_id: str
    choice: TurnDeliveryChoice
    prompt: PromptRequest
    handle: ConversationRuntimeHandle
    origin: Literal["browser", "worker"] = "browser"
    tracked_completion: asyncio.Future[TrackedTurnResult] | None = None
    before_prompt_started: TrackedTurnHook | None = None
    after_prompt_settled: TrackedTurnHook | None = None


@dataclass(slots=True)
class _Boundary:
    boundary_id: str
    trigger: ContextCompactionTrigger
    observation_identity: str | None


@dataclass(slots=True)
class _Command:
    operation: Callable[[_Actor], Awaitable[Any]]
    future: asyncio.Future[Any] | None


@dataclass(slots=True)
class _Actor:
    runtime: ConversationEmployeeRuntimePort
    publisher: ConversationRuntimeEventPublisher
    handle: ConversationRuntimeHandle
    integer_now: Callable[[], int]
    boundary_id_factory: Callable[[], str]
    cancel_timeout_seconds: float
    permission_broker: ConversationPermissionBroker | None
    terminal_service: ScopedAcpTerminalService | None
    commands: asyncio.Queue[_Command]
    queue: deque[QueuedPrompt] = field(default_factory=deque)
    queued_runtime_handles: dict[str, ConversationRuntimeHandle] = field(
        default_factory=dict
    )
    claimed_ids: set[str] = field(default_factory=set)
    observed_compactions: set[str] = field(default_factory=set)
    boundaries: list[_Boundary] = field(default_factory=list)
    enqueue_sequence: int = 0
    prompt_epoch: int = 0
    active: _ActivePrompt | None = None
    lifecycle: Literal["open", "closing", "failed", "closed"] = "open"
    runner: asyncio.Task[None] | None = None
    capture_task: asyncio.Task[ContextCompaction] | None = None
    capture_strategy: Any | None = None
    capture_tracked_handle: TrackedTurnHandle | None = None
    capture_tracked_terminal_hook: TrackedTurnHook | None = None
    capture_prompt_response: PromptResponse | None = None
    strategy_tasks: set[asyncio.Task[TurnDeliveryReceipt]] = field(default_factory=set)
    owner_cleanup_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    command_admission_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    accepting_commands: bool = True
    current_command: _Command | None = None
    prompt_started_hook: PromptStartedHook | None = None
    prompt_settlement_barrier: PromptSettlementBarrier | None = None
    compaction_transition_port: ConversationCompactionTransitionPort | None = None
    requested_cancel_transition_port: (
        ConversationRequestedCancelRecoveryTransitionPort | None
    ) = None
    actor_rekey: (
        Callable[
            [_Actor, ConversationRuntimeHandle, ConversationRuntimeHandle],
            Awaitable[None],
        ]
        | None
    ) = None
    capture_timeout_seconds: float = ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS
    requested_cancel_recovery_timeout_seconds: float = (
        ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS
    )
    capture_transition_token: CompactionTransitionToken | None = None
    capture_transition_deadline: float | None = None
    close_retirement_failure: ConversationTurnBrokerError | None = None

    async def run(self) -> None:
        try:
            while True:
                command = await self.commands.get()
                self.current_command = command
                try:
                    result = await command.operation(self)
                except asyncio.CancelledError as error:
                    if command.future is not None and not command.future.done():
                        command.future.set_exception(error)
                    raise
                except Exception as error:
                    if command.future is not None and not command.future.done():
                        command.future.set_exception(error)
                    if not isinstance(error, ConversationTurnBrokerError):
                        self._stop_after_generation_failure()
                else:
                    if command.future is not None and not command.future.done():
                        command.future.set_result(result)
                self.current_command = None
                if self.lifecycle == "closed" and self.commands.empty():
                    async with self.command_admission_lock:
                        if self.lifecycle == "closed" and self.commands.empty():
                            self.accepting_commands = False
                            return
        except asyncio.CancelledError:
            raise
        finally:
            self.accepting_commands = False
            current_command = self.current_command
            self.current_command = None
            if (
                current_command is not None
                and current_command.future is not None
                and not current_command.future.done()
            ):
                current_command.future.set_exception(
                    ConversationTurnBrokerError("conversation actor stopped")
                )
            while not self.commands.empty():
                queued_command = self.commands.get_nowait()
                if (
                    queued_command.future is not None
                    and not queued_command.future.done()
                ):
                    queued_command.future.set_exception(
                        ConversationTurnBrokerError("conversation actor stopped")
                    )

    def _stop_after_generation_failure(self) -> None:
        if self.lifecycle == "failed":
            return
        self.lifecycle = "failed"
        active, self.active = self.active, None
        if active is not None:
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult("errored", error="Conversation publication failed"),
            )
            for task in (
                active.task,
                active.cancel_task,
                active.timeout_task,
                active.permission_cancel_task,
            ):
                if task is not None and not task.done():
                    task.cancel()
        if self.capture_task is not None and not self.capture_task.done():
            self.capture_task.cancel()
        self.capture_task = None
        self.capture_strategy = None
        self._settle_tracked(
            self.capture_tracked_handle,
            self.capture_tracked_terminal_hook,
            TrackedTurnResult("errored", error="Conversation publication failed"),
        )
        self.capture_tracked_handle = None
        self.capture_tracked_terminal_hook = None
        self.capture_prompt_response = None
        for strategy_task in self.strategy_tasks:
            if not strategy_task.done():
                strategy_task.cancel()
        self.strategy_tasks.clear()
        self.queue.clear()
        self.queued_runtime_handles.clear()
        self.boundaries.clear()

        async def clean_failed_generation() -> None:
            if active is not None:
                await self._fail_requested_cancel_transition(
                    active, "Conversation publication failed"
                )
            await self._settle_generation_services(
                "Conversation publication failed"
            )

        cleanup = asyncio.create_task(
            clean_failed_generation(),
            name=(
                f"panels.acp.owner-cleanup.{self.handle.employee.employee_id}."
                f"{self.handle.child_generation}"
            ),
        )
        self.owner_cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._owner_cleanup_finished)
        while not self.commands.empty():
            queued_command = self.commands.get_nowait()
            if queued_command.future is not None and not queued_command.future.done():
                queued_command.future.set_exception(
                    ConversationTurnBrokerError(
                        "conversation publisher failed; delivery stopped"
                    )
                )

    def _owner_cleanup_finished(self, task: asyncio.Task[None]) -> None:
        self.owner_cleanup_tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            # The original publisher/runtime failure remains authoritative.
            # Cleanup is best-effort here and is awaited again by shutdown.
            return

    async def receipt(
        self,
        client_message_id: str,
        choice: TurnDeliveryChoice,
        state: Literal["accepted", "queued", "started", "interrupted", "rejected"],
        *,
        queue_position: int | None = None,
        reason: str | None = None,
    ) -> None:
        await self.publisher.publish_delivery_receipt(
            self.handle.employee,
            self.handle.binding,
            TurnDeliveryReceipt(
                client_message_id=client_message_id,
                choice=choice,
                state=state,
                queue_position=queue_position,
                reason=reason,
            ),
        )

    async def activity(self, state: ConversationActivityState, detail: str) -> None:
        await self.publisher.publish_activity(
            self.handle.employee, self.handle.binding, state, detail
        )

    async def _start(
        self,
        submission: _QueuedSubmission,
        *,
        publish_accepted: bool,
    ) -> TrackedTurnHandle | None:
        submitted_handle = submission.handle
        if not self._matches_runtime(submitted_handle) or self.lifecycle != "open":
            raise ConversationTurnBrokerError("stale conversation runtime handle")
        if publish_accepted:
            await self.receipt(submission.client_message_id, submission.choice, "accepted")
        if not self._matches_runtime(submitted_handle) or self.lifecycle != "open":
            raise ConversationTurnBrokerError("conversation stopped before prompt start")
        lease = await self.runtime.acquire_runtime_lease(submitted_handle)
        delivered_prompt = self._prompt_for_display(submission.prompt)
        original_blocks = tuple(submission.prompt.prompt)
        delivered_blocks = tuple(delivered_prompt.prompt)
        if submission.origin == "worker":
            await self.publisher.publish_programmatic_prompt(
                self.handle.employee,
                self.handle.binding,
                ProgrammaticPrompt(
                    prompt_id=submission.client_message_id,
                    prompt=delivered_prompt,
                    source="worker",
                ),
            )
        elif (
            len(delivered_blocks) > len(original_blocks)
            and delivered_blocks[-len(original_blocks) :] == original_blocks
        ):
            role_prompt = delivered_prompt.model_copy(
                update={"prompt": list(delivered_blocks[: -len(original_blocks)])}
            )
            await self.publisher.publish_programmatic_prompt(
                self.handle.employee,
                self.handle.binding,
                ProgrammaticPrompt(
                    prompt_id=f"{submission.client_message_id}:role",
                    prompt=role_prompt,
                    source="role",
                ),
            )
        self.prompt_epoch += 1
        epoch = self.prompt_epoch
        if self.prompt_started_hook is not None:
            self.prompt_started_hook(submitted_handle, epoch)

        tracked_handle: TrackedTurnHandle | None = None
        if submission.tracked_completion is not None:
            tracked_handle = TrackedTurnHandle(
                employee_id=submitted_handle.employee.employee_id,
                acp_session_id=submitted_handle.binding.acp_session_id,
                binding_generation=submitted_handle.binding.binding_generation,
                child_generation=submitted_handle.child_generation,
                record_identity=submitted_handle.record_identity,
                client_message_id=submission.client_message_id,
                prompt_epoch=epoch,
                completion=submission.tracked_completion,
            )
            if submission.before_prompt_started is not None:
                submission.before_prompt_started(tracked_handle)

        is_explicit = self._is_explicit_compaction(submission.prompt)
        if is_explicit and submitted_handle.definition.turn_capabilities.observes_compaction:
            boundary = _Boundary(
                boundary_id=self.boundary_id_factory(),
                trigger="explicit",
                observation_identity=None,
            )
            self.boundaries.append(boundary)
            await self.publisher.publish_compaction(
                self.handle.employee,
                self.handle.binding,
                ContextCompaction(
                    boundary_id=boundary.boundary_id,
                    state="compacting",
                    trigger="explicit",
                ),
            )
            await self.activity("compacting", "Compacting conversation context")

        prompt_task = asyncio.create_task(
            lease.prompt(delivered_prompt),
            name=(
                f"panels.acp.prompt.{self.handle.employee.employee_id}."
                f"{submitted_handle.child_generation}.{epoch}"
            ),
        )
        active = _ActivePrompt(
            epoch=epoch,
            client_message_id=submission.client_message_id,
            choice=submission.choice,
            prompt=delivered_prompt,
            lease=lease,
            task=prompt_task,
            origin=submission.origin,
            tracked_handle=tracked_handle,
            tracked_terminal_hook=submission.after_prompt_settled,
        )
        self.active = active

        def prompt_completed(task: asyncio.Task[PromptResponse]) -> None:
            async def settle(actor: _Actor) -> None:
                await actor._settle_prompt(epoch, task)

            self._enqueue_background(settle)

        prompt_task.add_done_callback(prompt_completed)
        await self.receipt(submission.client_message_id, submission.choice, "started")
        if not is_explicit:
            await self.activity("thinking", "Employee is responding")
        return tracked_handle

    def _prompt_for_display(self, prompt: PromptRequest) -> PromptRequest:
        prepare = getattr(self.handle.child, "prompt_for_display", None)
        if prepare is None:
            return prompt
        displayed = prepare(prompt)
        if not isinstance(displayed, PromptRequest):
            raise ConversationTurnBrokerError(
                "prompt display preparation returned an invalid ACP prompt"
            )
        return displayed

    @staticmethod
    def _settle_tracked(
        active_handle: TrackedTurnHandle | None,
        terminal_hook: TrackedTurnHook | None,
        result: TrackedTurnResult,
    ) -> None:
        if active_handle is None:
            return
        if active_handle.completion.done():
            return
        active_handle.completion.set_result(result)
        if terminal_hook is not None:
            terminal_hook(active_handle)

    async def _settle_prompt(self, epoch: int, task: asyncio.Task[PromptResponse]) -> None:
        active = self.active
        if active is None or active.epoch != epoch or active.task is not task:
            return
        cancellation_cause = active.cancellation_cause
        if cancellation_cause is not None and cancellation_cause not in {
            "new_conversation",
            "shutdown",
        }:
            if active.cancel_task is not None and not active.cancel_task.done():
                return
            if (
                active.permission_cancel_task is not None
                and not active.permission_cancel_task.done()
            ):
                return
            for cancellation_task in (
                active.cancel_task,
                active.permission_cancel_task,
            ):
                if cancellation_task is None:
                    continue
                if cancellation_task.cancelled():
                    return
                try:
                    cancellation_task.result()
                except Exception:
                    # The task's own settlement callback keeps cancellation-delivery
                    # and permission-cancellation failures authoritative.
                    return
        if active.timeout_task is not None:
            active.timeout_task.cancel()
        response: PromptResponse | None = None
        prompt_raised = False
        try:
            response = task.result()
        except asyncio.CancelledError:
            if cancellation_cause is None:
                await self._fail_generation(
                    "Employee prompt was cancelled unexpectedly",
                    failure_provenance="backend",
                )
                return
            prompt_raised = True
        except Exception as error:
            if cancellation_cause not in {
                "user",
                "send_now",
                "new_conversation",
                "shutdown",
            }:
                provider_reason: object = None
                if cancellation_cause is None and self.boundaries:
                    strategy = active.lease.handle.definition.turn_strategy
                    prompt_failure_reason = getattr(
                        strategy, "prompt_failure_reason", None
                    )
                    if prompt_failure_reason is not None:
                        try:
                            provider_reason = prompt_failure_reason(
                                active.lease.handle.binding,
                                active.prompt,
                                error,
                            )
                        except Exception:
                            pass
                await self._fail_generation(
                    _backend_failure_reason(error, provider_reason),
                    failure_provenance="backend",
                )
                return
            prompt_raised = True

        turn_capabilities = active.lease.handle.definition.turn_capabilities
        requires_requested_cancel_recovery = (
            response is not None
            and cancellation_cause in {"user", "send_now"}
            and turn_capabilities.requires_fresh_child_after_requested_cancel
        )
        if (
            response is not None
            and active.requested_cancel_transition_token is not None
            and not requires_requested_cancel_recovery
        ):
            try:
                await self._resume_requested_cancelled_runtime(active)
            except Exception:
                await self._fail_generation(
                    "Cancellation quarantine could not be resumed",
                    active.requested_cancel_deadline,
                )
                return

        if self.prompt_settlement_barrier is not None:
            admitted = await self.prompt_settlement_barrier(
                active.lease.handle, epoch
            )
            for notification in admitted.notifications:
                await self._observe_session_notification(notification)
            if admitted.rejection_reason is not None:
                await self._fail_generation(
                    admitted.rejection_reason,
                    active.requested_cancel_deadline or active.close_deadline,
                    failure_provenance="backend",
                )
                return

        if (
            prompt_raised and cancellation_cause in {"user", "send_now"}
        ) or requires_requested_cancel_recovery:
            await self._recover_after_requested_cancel(active, response)
            return

        if prompt_raised and cancellation_cause in {"new_conversation", "shutdown"}:
            deadline = active.close_deadline
            if deadline is None:
                await self._fail_generation("Conversation close lost its deadline")
                return
            for cancellation_task in (
                active.cancel_task,
                active.permission_cancel_task,
            ):
                if cancellation_task is not None and not cancellation_task.done():
                    cancellation_task.cancel()
            await self._interrupt_active(active, "Prompt interrupted")
            try:
                await self._await_before_deadline(
                    self.runtime.retire_runtime_lease(active.lease, deadline),
                    deadline,
                )
            except asyncio.CancelledError:
                try:
                    await self.runtime.fail_runtime_handle(active.lease.handle)
                except BaseException:
                    pass
                raise
            except Exception:
                try:
                    await self.runtime.fail_runtime_handle(active.lease.handle)
                except BaseException:
                    pass
                self.close_retirement_failure = ConversationTurnBrokerError(
                    "Conversation runtime retirement failed"
                )
                await self._fail_generation(
                    "Conversation runtime retirement failed", deadline
                )
                return
            self.active = None
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult("interrupted"),
            )
            return

        cause = cancellation_cause
        if cause is not None or (response is not None and response.stop_reason == "cancelled"):
            await self._interrupt_active(active, "Prompt interrupted")

        self.active = None
        if cause in {"new_conversation", "shutdown", "child_failure"}:
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult(
                    "errored" if cause == "child_failure" else "interrupted",
                    response=response,
                    error="Employee connection failed" if cause == "child_failure" else None,
                    failure_provenance=(
                        "backend" if cause == "child_failure" else None
                    ),
                ),
            )
            return
        if cause == "send_now":
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult("interrupted", response=response),
            )
            successor = active.successor
            if successor is not None:
                await self._start(successor, publish_accepted=False)
            return
        if cause == "user" or (
            cause is None and response is not None and response.stop_reason == "cancelled"
        ):
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult("interrupted", response=response),
            )
            await self._fail_boundaries("Compaction was interrupted")
            await self.activity("interrupted", "Employee prompt was interrupted")
            await self.activity("idle", "Employee is ready")
            await self._advance_queue()
            return

        if self.boundaries:
            await self._settle_generation_services(
                "Compaction is changing the durable session binding"
            )
            strategy = self.runtime.strategy_for_lease(active.lease)
            self.capture_strategy = strategy
            self.capture_tracked_handle = active.tracked_handle
            self.capture_tracked_terminal_hook = active.tracked_terminal_hook
            self.capture_prompt_response = response
            capture_deadline = (
                asyncio.get_running_loop().time() + self.capture_timeout_seconds
            )
            self.capture_transition_deadline = capture_deadline
            if self.compaction_transition_port is not None:
                try:
                    self.capture_transition_token = cast(
                        CompactionTransitionToken,
                        await self._await_before_deadline(
                            self.compaction_transition_port.begin_compaction_transition(
                                self.handle, capture_deadline
                            ),
                            capture_deadline,
                        ),
                    )
                except ConversationWaitDeadlineExpired:
                    deadline_failure = ConversationCompactionCaptureDeadlineExpired(
                        phase="hub admission",
                        capture_timeout_seconds=self.capture_timeout_seconds,
                        durable_binding_disposition="stayed",
                        original_binding_generation=(
                            self.handle.binding.binding_generation
                        ),
                        generation_fatal=False,
                    )
                    failure_reason = str(deadline_failure)
                    logger.error("%s", failure_reason)
                    self.capture_transition_deadline = None
                    self.capture_strategy = None
                    self.capture_tracked_handle = None
                    self.capture_tracked_terminal_hook = None
                    self.capture_prompt_response = None
                    self._settle_tracked(
                        active.tracked_handle,
                        active.tracked_terminal_hook,
                        TrackedTurnResult(
                            "errored",
                            response=response,
                            error=failure_reason,
                        ),
                    )
                    await self._fail_boundaries(failure_reason)
                    await self.activity("idle", "Employee is ready")
                    await self._advance_queue()
                    return
                except Exception as error:
                    failure_reason = str(
                        ConversationCompactionCaptureFailed(
                            phase="hub admission",
                            primary_error=error,
                            generation_fatal=False,
                        )
                    )
                    logger.error("%s", failure_reason)
                    self.capture_transition_deadline = None
                    self.capture_strategy = None
                    self.capture_tracked_handle = None
                    self.capture_tracked_terminal_hook = None
                    self.capture_prompt_response = None
                    self._settle_tracked(
                        active.tracked_handle,
                        active.tracked_terminal_hook,
                        TrackedTurnResult(
                            "errored",
                            response=response,
                            error=failure_reason,
                        ),
                    )
                    await self._fail_boundaries(failure_reason)
                    await self.activity("idle", "Employee is ready")
                    await self._advance_queue()
                    return
            exact_capture = getattr(
                strategy, "capture_compaction_with_deadline", None
            )
            capture_task = asyncio.create_task(
                (
                    exact_capture(
                        self.handle.binding,
                        capture_deadline,
                        self.capture_timeout_seconds,
                        tuple(
                            ConversationCompactionBoundaryProvenance(
                                boundary_id=boundary.boundary_id,
                                trigger=boundary.trigger,
                            )
                            for boundary in self.boundaries
                        ),
                    )
                    if exact_capture is not None
                    else strategy.capture_compaction(self.handle.binding)
                ),
                name=f"panels.acp.capture.{self.handle.employee.employee_id}.{epoch}",
            )
            self.capture_task = capture_task

            def capture_completed(completed: asyncio.Task[ContextCompaction]) -> None:
                async def finish(actor: _Actor) -> None:
                    await actor._finish_capture(epoch, completed)

                self._enqueue_background(finish)

            capture_task.add_done_callback(capture_completed)
            return

        self._settle_tracked(
            active.tracked_handle,
            active.tracked_terminal_hook,
            TrackedTurnResult("complete", response=response),
        )
        await self.activity("idle", "Employee is ready")
        await self._advance_queue()

    async def _finish_capture(self, epoch: int, task: asyncio.Task[ContextCompaction]) -> None:
        if self.capture_task is not task or self.prompt_epoch != epoch:
            return
        self.capture_task = None
        strategy, self.capture_strategy = self.capture_strategy, None
        tracked_handle, self.capture_tracked_handle = self.capture_tracked_handle, None
        terminal_hook, self.capture_tracked_terminal_hook = (
            self.capture_tracked_terminal_hook,
            None,
        )
        prompt_response, self.capture_prompt_response = self.capture_prompt_response, None
        transition_token, self.capture_transition_token = (
            self.capture_transition_token,
            None,
        )
        capture_deadline, self.capture_transition_deadline = (
            self.capture_transition_deadline,
            None,
        )
        capture_failed = False
        generation_fatal = False
        try:
            result = task.result()
        except asyncio.CancelledError:
            capture_failed = True
            result = ContextCompaction(
                boundary_id="capture-result",
                state="failed",
                trigger="automatic",
                reason="Conversation context capture was cancelled",
            )
        except ConversationCompactionCaptureDeadlineExpired as error:
            capture_failed = True
            generation_fatal = error.generation_fatal
            failure_reason = str(error)
            logger.error("%s", failure_reason)
            result = ContextCompaction(
                boundary_id="capture-result",
                state="failed",
                trigger="automatic",
                reason=failure_reason,
            )
        except ConversationCompactionCaptureFailed as error:
            capture_failed = True
            generation_fatal = error.generation_fatal
            failure_reason = str(error)
            logger.error("%s", failure_reason)
            result = ContextCompaction(
                boundary_id="capture-result",
                state="failed",
                trigger="automatic",
                reason=failure_reason,
            )
        except ConversationRuntimeGenerationFatal as error:
            capture_failed = True
            generation_fatal = True
            failure_reason = str(
                ConversationCompactionCaptureFailed(
                    phase="normalization",
                    primary_error=error,
                    generation_fatal=True,
                )
            )
            logger.error("%s", failure_reason)
            result = ContextCompaction(
                boundary_id="capture-result",
                state="failed",
                trigger="automatic",
                reason=failure_reason,
            )
        except Exception as error:
            capture_failed = True
            failure_reason = str(
                ConversationCompactionCaptureFailed(
                    phase="normalization",
                    primary_error=error,
                    generation_fatal=False,
                )
            )
            logger.error("%s", failure_reason)
            result = ContextCompaction(
                boundary_id="capture-result",
                state="failed",
                trigger="automatic",
                reason=failure_reason,
            )
        transition: CompactionCaptureTransition | None = None
        take_transition = getattr(strategy, "take_capture_transition", None)
        if take_transition is not None:
            transition = cast(CompactionCaptureTransition | None, take_transition())
        if transition is not None:
            previous_handle = self.handle
            replacement = transition.replacement_handle
            if (
                replacement.employee.employee_id
                != previous_handle.employee.employee_id
                or replacement.binding.employee_id
                != previous_handle.binding.employee_id
                or replacement.binding.binding_generation
                != previous_handle.binding.binding_generation + 1
                or replacement.binding.acp_session_id
                == previous_handle.binding.acp_session_id
            ):
                invalid_replacement_error = ConversationTurnBrokerError(
                    "compaction replacement returned an invalid runtime"
                )
                failure_reason = str(
                    ConversationCompactionCaptureFailed(
                        phase="actor rekey",
                        primary_error=invalid_replacement_error,
                        generation_fatal=True,
                    )
                )
                logger.error("%s", failure_reason)
                self._settle_tracked(
                    tracked_handle,
                    terminal_hook,
                    TrackedTurnResult(
                        "errored",
                        response=prompt_response,
                        error=failure_reason,
                    ),
                )
                await self._fail_compaction_transition(
                    transition_token,
                    failure_reason,
                )
                await self._fail_generation(failure_reason, capture_deadline)
                return
            retargeted: deque[QueuedPrompt] = deque()
            for queued in self.queue:
                retargeted.append(
                    queued.model_copy(
                        update={
                            "prompt": queued.prompt.model_copy(
                                update={
                                    "session_id": replacement.binding.acp_session_id
                                }
                            )
                        }
                    )
                )
            self.queue = retargeted
            self.queued_runtime_handles = {
                queued.client_message_id: replacement for queued in self.queue
            }
            transition_phase: Literal["actor rekey", "browser commit"] = "actor rekey"
            try:
                if self.actor_rekey is None:
                    raise ConversationTurnBrokerError(
                        "compaction actor rekey owner is not bound"
                    )
                if capture_deadline is None:
                    raise ConversationTurnBrokerError(
                        "compaction transition deadline is missing"
                    )
                await self._await_before_deadline(
                    self.actor_rekey(self, previous_handle, replacement),
                    capture_deadline,
                )
                self.handle = replacement
                transition_phase = "browser commit"
                if self.compaction_transition_port is not None:
                    if transition_token is None:
                        raise ConversationTurnBrokerError(
                            "compaction transition token is missing"
                        )
                    await self._await_before_deadline(
                        self.compaction_transition_port.commit_compaction_transition(
                            transition_token,
                            replacement,
                            transition.replay,
                            tuple(self.queue),
                        ),
                        capture_deadline,
                    )
            except ConversationWaitDeadlineExpired:
                deadline_failure = ConversationCompactionCaptureDeadlineExpired(
                    phase=transition_phase,
                    capture_timeout_seconds=self.capture_timeout_seconds,
                    durable_binding_disposition="committed",
                    original_binding_generation=(
                        previous_handle.binding.binding_generation
                    ),
                    generation_fatal=True,
                )
                failure_reason = str(deadline_failure)
                logger.error("%s", failure_reason)
                self._settle_tracked(
                    tracked_handle,
                    terminal_hook,
                    TrackedTurnResult(
                        "errored",
                        response=prompt_response,
                        error=failure_reason,
                    ),
                )
                await self.runtime.fail_runtime_handle(replacement)
                await self._fail_compaction_transition(
                    transition_token, failure_reason
                )
                try:
                    await self._fail_generation(failure_reason, capture_deadline)
                except Exception:
                    self._stop_after_generation_failure()
                return
            except Exception as error:
                failure_reason = str(
                    ConversationCompactionCaptureFailed(
                        phase=transition_phase,
                        primary_error=error,
                        generation_fatal=True,
                    )
                )
                logger.error("%s", failure_reason)
                self._settle_tracked(
                    tracked_handle,
                    terminal_hook,
                    TrackedTurnResult(
                        "errored",
                        response=prompt_response,
                        error=failure_reason,
                    ),
                )
                await self.runtime.fail_runtime_handle(replacement)
                await self._fail_compaction_transition(
                    transition_token, failure_reason
                )
                try:
                    await self._fail_generation(failure_reason, capture_deadline)
                except Exception:
                    self._stop_after_generation_failure()
                return
            if not transition.fork_won:
                capture_failed = True
        else:
            if generation_fatal:
                fatal_reason = (
                    result.reason
                    or "Conversation runtime generation failed during compaction"
                )
                await self._fail_compaction_transition(
                    transition_token, fatal_reason
                )
                self._settle_tracked(
                    tracked_handle,
                    terminal_hook,
                    TrackedTurnResult(
                        "errored",
                        response=prompt_response,
                        error=fatal_reason,
                    ),
                )
                await self._fail_generation(fatal_reason, capture_deadline)
                return
            await self._abort_compaction_transition(transition_token)
        try:
            boundaries, self.boundaries = self.boundaries, []
            for boundary in boundaries:
                if result.state == "compacted":
                    if transition is not None:
                        continue
                    normalized = ContextCompaction(
                        boundary_id=boundary.boundary_id,
                        state="compacted",
                        trigger=boundary.trigger,
                    )
                else:
                    normalized = ContextCompaction(
                        boundary_id=boundary.boundary_id,
                        state="failed",
                        trigger=boundary.trigger,
                        reason=result.reason or "Conversation context capture failed",
                    )
                await self.publisher.publish_compaction(
                    self.handle.employee, self.handle.binding, normalized
                )
                if normalized.state == "failed":
                    capture_failed = True
            self._settle_tracked(
                tracked_handle,
                terminal_hook,
                TrackedTurnResult(
                    "errored" if capture_failed else "complete",
                    response=prompt_response,
                    error=(
                        result.reason or "Conversation context capture failed"
                        if capture_failed
                        else None
                    ),
                ),
            )
            await self.activity("idle", "Employee is ready")
            await self._advance_queue()
            if transition_token is not None:
                await self._complete_compaction_transition(
                    transition_token,
                    self.handle,
                    capture_deadline,
                )
        except ConversationWaitDeadlineExpired:
            deadline_failure = ConversationCompactionCaptureDeadlineExpired(
                phase="browser commit",
                capture_timeout_seconds=self.capture_timeout_seconds,
                durable_binding_disposition=(
                    "committed" if transition is not None else "stayed"
                ),
                original_binding_generation=(
                    transition_token.original_handle.binding.binding_generation
                    if transition_token is not None
                    else self.handle.binding.binding_generation
                ),
                generation_fatal=transition is not None,
            )
            failure_reason = str(deadline_failure)
            logger.error("%s", failure_reason)
            if transition is not None:
                await self.runtime.fail_runtime_handle(transition.replacement_handle)
            await self._fail_compaction_transition(transition_token, failure_reason)
            try:
                await self._fail_generation(failure_reason, capture_deadline)
            except Exception:
                self._stop_after_generation_failure()
        except Exception as error:
            failure_reason = str(
                ConversationCompactionCaptureFailed(
                    phase="browser commit",
                    primary_error=error,
                    generation_fatal=transition is not None,
                )
            )
            logger.error("%s", failure_reason)
            self._settle_tracked(
                tracked_handle,
                terminal_hook,
                TrackedTurnResult(
                    "errored",
                    response=prompt_response,
                    error=failure_reason,
                ),
            )
            if transition is not None:
                await self.runtime.fail_runtime_handle(transition.replacement_handle)
            await self._fail_compaction_transition(transition_token, failure_reason)
            try:
                await self._fail_generation(failure_reason, capture_deadline)
            except Exception:
                self._stop_after_generation_failure()

    async def _complete_compaction_transition(
        self,
        token: CompactionTransitionToken | None,
        settled_handle: ConversationRuntimeHandle,
        deadline: float | None,
    ) -> None:
        if token is None or deadline is None or self.compaction_transition_port is None:
            return
        await self._await_before_deadline(
            self.compaction_transition_port.complete_compaction_transition(
                token, settled_handle
            ),
            deadline,
        )

    async def _abort_compaction_transition(
        self, token: CompactionTransitionToken | None
    ) -> None:
        if token is None or self.compaction_transition_port is None:
            return
        try:
            await self._await_before_deadline(
                self.compaction_transition_port.abort_compaction_transition(token),
                token.deadline,
            )
        except Exception:
            # Expiry has its own synchronous wake path. Capture failure remains
            # the visible result when the token has already expired.
            return

    async def _fail_compaction_transition(
        self,
        token: CompactionTransitionToken | None,
        reason: str,
    ) -> None:
        if token is None or self.compaction_transition_port is None:
            return
        try:
            await self._await_before_deadline(
                self.compaction_transition_port.fail_compaction_transition(token, reason),
                token.deadline,
            )
        except Exception:
            # Expiry and shutdown already wake waiters through the hub's
            # synchronous failure path.
            return

    @staticmethod
    async def _await_before_deadline(
        operation: Awaitable[Any], deadline: float
    ) -> Any:
        task = asyncio.ensure_future(operation)
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            done, _pending = await asyncio.wait({task}, timeout=remaining)
        except BaseException:
            if not task.done():
                task.cancel()
                task.add_done_callback(_Actor._consume_late_deadline_result)
            raise
        if task not in done:
            task.cancel()
            task.add_done_callback(_Actor._consume_late_deadline_result)
            raise ConversationWaitDeadlineExpired(
                "conversation compaction transition exceeded its deadline"
            )
        return task.result()

    @staticmethod
    def _consume_late_deadline_result(task: asyncio.Future[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except BaseException:
            return

    async def _advance_queue(self) -> None:
        if self.lifecycle != "open" or self.active is not None or not self.queue:
            return
        queued = self.queue.popleft()
        queued_handle = self.queued_runtime_handles.pop(
            queued.client_message_id, self.handle
        )
        await self.publisher.publish_queue_snapshot(
            self.handle.employee, self.handle.binding, tuple(self.queue)
        )
        await self._start(
            _QueuedSubmission(
                queued.client_message_id, "queue", queued.prompt, queued_handle
            ),
            publish_accepted=False,
        )

    async def _interrupt_active(self, active: _ActivePrompt, reason: str) -> None:
        if active.interrupted_published:
            return
        active.interrupted_published = True
        await self.receipt(
            active.client_message_id,
            active.choice,
            "interrupted",
            reason=reason,
        )

    async def _resume_requested_cancelled_runtime(
        self, active: _ActivePrompt
    ) -> None:
        token = active.requested_cancel_transition_token
        port = self.requested_cancel_transition_port
        if token is None or port is None:
            raise ConversationTurnBrokerError(
                "requested-cancel transition is not available"
            )
        await self._await_before_deadline(
            port.resume_requested_cancelled_runtime(token), token.deadline
        )
        active.requested_cancel_transition_token = None

    async def _fail_requested_cancel_transition(
        self, active: _ActivePrompt, reason: str
    ) -> None:
        token = active.requested_cancel_transition_token
        port = self.requested_cancel_transition_port
        if token is None or port is None:
            return
        active.requested_cancel_transition_token = None
        try:
            await self._await_before_deadline(
                port.fail_requested_cancel_recovery_transition(token, reason),
                token.deadline,
            )
        except Exception:
            # Deadline expiry and hub shutdown synchronously settle their token.
            return

    async def _recover_after_requested_cancel(
        self,
        active: _ActivePrompt,
        response: PromptResponse | None,
    ) -> None:
        token = active.requested_cancel_transition_token
        port = self.requested_cancel_transition_port
        deadline = active.requested_cancel_deadline
        if token is None or port is None or deadline is None:
            await self._fail_generation(
                "Requested cancellation recovery is unavailable", deadline
            )
            return
        replacement_handle: ConversationRuntimeHandle | None = None
        try:
            await self._interrupt_active(active, "Prompt interrupted")
            await self._settle_generation_services("Prompt was cancelled", deadline)
            replacement = await self._await_before_deadline(
                self.runtime.replace_runtime_after_requested_cancel(
                    active.lease, deadline
                ),
                deadline,
            )
            replacement_handle = replacement.replacement_handle
            original = active.lease.handle
            if (
                replacement_handle.employee != original.employee
                or replacement_handle.binding != original.binding
                or replacement_handle.child_generation <= original.child_generation
                or replacement_handle.child is original.child
                or replacement_handle.record_identity is original.record_identity
            ):
                raise ConversationTurnBrokerError(
                    "requested-cancel replacement returned an invalid runtime"
                )

            for client_message_id in tuple(self.queued_runtime_handles):
                self.queued_runtime_handles[client_message_id] = replacement_handle
            successor = active.successor
            if successor is not None:
                successor = _QueuedSubmission(
                    client_message_id=successor.client_message_id,
                    choice=successor.choice,
                    prompt=successor.prompt,
                    handle=replacement_handle,
                    origin=successor.origin,
                    tracked_completion=successor.tracked_completion,
                    before_prompt_started=successor.before_prompt_started,
                    after_prompt_settled=successor.after_prompt_settled,
                )
            self.handle = replacement_handle
            send_now_successor_human_echo = (
                HumanEcho(
                    client_message_id=successor.client_message_id,
                    prompt=successor.prompt,
                )
                if active.cancellation_cause == "send_now" and successor is not None
                else None
            )
            await self._await_before_deadline(
                port.commit_requested_cancel_recovery_transition(
                    token,
                    replacement_handle,
                    replacement.replay,
                    tuple(self.queue),
                    send_now_successor_human_echo,
                ),
                deadline,
            )
            active.requested_cancel_transition_token = None
            self.active = None
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult("interrupted", response=response),
            )
            if active.cancellation_cause == "send_now":
                if successor is not None:
                    await self._start(successor, publish_accepted=False)
                return
            await self._fail_boundaries("Compaction was interrupted")
            await self.activity("interrupted", "Employee prompt was interrupted")
            await self.activity("idle", "Employee is ready")
            await self._advance_queue()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._fail_requested_cancel_transition(
                active, "Requested cancellation recovery failed"
            )
            try:
                if replacement_handle is not None:
                    await self.runtime.fail_runtime_handle(replacement_handle)
                else:
                    await self.runtime.fail_runtime_handle(active.lease.handle)
            except Exception:
                # Generation failure below remains authoritative and settles
                # all visible intent even when runtime invalidation itself fails.
                pass
            await self._fail_generation("Employee connection failed", deadline)

    async def _begin_cancel(
        self,
        cause: _CancellationCause,
        successor: _QueuedSubmission | None,
        close_deadline: float | None = None,
    ) -> None:
        active = self.active
        if active is None or active.cancellation_cause is not None:
            raise ConversationTurnBrokerError("no cancellable active prompt")
        active.cancellation_cause = cause
        active.successor = successor
        if cause in {"user", "send_now"}:
            port = self.requested_cancel_transition_port
            deadline = (
                asyncio.get_running_loop().time()
                + self.requested_cancel_recovery_timeout_seconds
            )
            active.requested_cancel_deadline = deadline
            if port is not None:
                try:
                    active.requested_cancel_transition_token = cast(
                        RequestedCancelRecoveryTransitionToken,
                        await self._await_before_deadline(
                            port.begin_requested_cancel_recovery_transition(
                                active.lease.handle, deadline
                            ),
                            deadline,
                        ),
                    )
                except Exception:
                    active.cancellation_cause = None
                    active.successor = None
                    active.requested_cancel_deadline = None
                    raise
        else:
            active.close_deadline = close_deadline
        active.timeout_task = asyncio.create_task(
            self._cancel_timeout(active.epoch),
            name=f"panels.acp.cancel-timeout.{self.handle.employee.employee_id}.{active.epoch}",
        )
        if self.permission_broker is not None:
            active.permission_cancel_task = asyncio.create_task(
                self.permission_broker.cancel_prompt_epoch(
                    self.handle.employee.employee_id,
                    self.handle.binding.binding_generation,
                    self.handle.child_generation,
                    self.handle.record_identity,
                    active.epoch,
                    "Prompt was cancelled",
                ),
                name=(
                    f"panels.acp.permission-cancel."
                    f"{self.handle.employee.employee_id}.{active.epoch}"
                ),
            )

            def permission_cancel_finished(task: asyncio.Task[None]) -> None:
                async def settle(actor: _Actor) -> None:
                    await actor._settle_permission_cancel(active.epoch, task)

                self._enqueue_background(settle)

            active.permission_cancel_task.add_done_callback(
                permission_cancel_finished
            )
        active.cancel_task = asyncio.create_task(
            active.lease.cancel(CancelNotification(session_id=self.handle.binding.acp_session_id)),
            name=f"panels.acp.cancel.{self.handle.employee.employee_id}.{active.epoch}",
        )

        def cancel_finished(task: asyncio.Task[None]) -> None:
            async def settle(actor: _Actor) -> None:
                await actor._settle_cancel_send(active.epoch, task)

            self._enqueue_background(settle)

        active.cancel_task.add_done_callback(cancel_finished)

    async def _settle_cancel_send(self, epoch: int, task: asyncio.Task[None]) -> None:
        active = self.active
        if active is None or active.epoch != epoch or task.cancelled():
            return
        try:
            task.result()
        except Exception:
            await self._fail_requested_cancel_transition(
                active, "Employee cancellation delivery failed"
            )
            await self._fail_generation(
                "Employee cancellation delivery failed",
                active.requested_cancel_deadline or active.close_deadline,
            )
            return
        if active.task.done() and (
            active.permission_cancel_task is None
            or active.permission_cancel_task.done()
        ):
            await self._settle_prompt(epoch, active.task)

    async def _settle_permission_cancel(
        self, epoch: int, task: asyncio.Task[None]
    ) -> None:
        active = self.active
        if active is None or active.epoch != epoch or task.cancelled():
            return
        try:
            task.result()
        except Exception:
            await self._fail_requested_cancel_transition(
                active, "Permission cancellation failed"
            )
            await self._fail_generation(
                "Permission cancellation failed",
                active.requested_cancel_deadline or active.close_deadline,
            )
            return
        if active.task.done() and (
            active.cancel_task is None or active.cancel_task.done()
        ):
            await self._settle_prompt(epoch, active.task)

    async def _cancel_timeout(self, epoch: int) -> None:
        await asyncio.sleep(self.cancel_timeout_seconds)
        self._enqueue_background(lambda actor: actor._cancel_timed_out(epoch))

    async def _cancel_timed_out(self, epoch: int) -> None:
        active = self.active
        if active is None or active.epoch != epoch:
            return
        if active.cancel_task is not None:
            active.cancel_task.cancel()
        if active.permission_cancel_task is not None:
            active.permission_cancel_task.cancel()
        await self._fail_requested_cancel_transition(
            active, "Prompt cancellation timed out"
        )
        await self._interrupt_active(active, "Prompt cancellation timed out")
        self._settle_tracked(
            active.tracked_handle,
            active.tracked_terminal_hook,
            TrackedTurnResult(
                "errored",
                error="Prompt cancellation timed out",
                failure_provenance="conversation",
            ),
        )
        if active.successor is not None:
            await self.receipt(
                active.successor.client_message_id,
                active.successor.choice,
                "rejected",
                reason="Employee state is indeterminate after cancellation timeout",
            )
        self.active = None
        await self._reject_queue("Employee state is indeterminate after cancellation timeout")
        self.lifecycle = "failed"
        deadline = active.requested_cancel_deadline or active.close_deadline
        if deadline is None:
            deadline = asyncio.get_running_loop().time()
        try:
            await self._await_before_deadline(
                self.runtime.retire_runtime_lease(active.lease, deadline),
                deadline,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await self.runtime.fail_runtime_handle(active.lease.handle)
            except BaseException:
                pass
        if not active.task.done():
            active.task.cancel()
        try:
            await self._settle_generation_services(
                "Employee cancellation timed out", deadline
            )
        except BaseException:
            pass
        await self.activity("failed", "Employee connection failed")

    async def _fail_generation(
        self,
        reason: str,
        deadline: float | None = None,
        *,
        failure_provenance: TrackedTurnFailureProvenance = "conversation",
    ) -> None:
        if self.lifecycle == "failed":
            return
        self.lifecycle = "failed"
        active = self.active
        self.active = None
        if active is not None:
            await self._fail_requested_cancel_transition(active, reason)
            if active.timeout_task is not None:
                active.timeout_task.cancel()
            if active.cancel_task is not None:
                active.cancel_task.cancel()
            if active.permission_cancel_task is not None:
                active.permission_cancel_task.cancel()
            if not active.task.done():
                active.task.cancel()
            await self._interrupt_active(active, reason)
            self._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult(
                    "errored",
                    error=reason,
                    failure_provenance=failure_provenance,
                ),
            )
            if active.successor is not None:
                await self.receipt(
                    active.successor.client_message_id,
                    active.successor.choice,
                    "rejected",
                    reason=reason,
                )
        await self._reject_queue(reason)
        if self.capture_task is not None:
            self.capture_task.cancel()
            self.capture_task = None
        self.capture_strategy = None
        self._settle_tracked(
            self.capture_tracked_handle,
            self.capture_tracked_terminal_hook,
            TrackedTurnResult(
                "errored",
                error=reason,
                failure_provenance=failure_provenance,
            ),
        )
        self.capture_tracked_handle = None
        self.capture_tracked_terminal_hook = None
        self.capture_prompt_response = None
        await self._fail_boundaries(reason)
        settlement_error: Exception | None = None
        try:
            await self._settle_generation_services(reason, deadline)
        except Exception as error:
            settlement_error = error
        await self.activity("failed", reason)
        if settlement_error is not None:
            raise settlement_error

    async def _settle_generation_services(
        self, reason: str, deadline: float | None = None
    ) -> None:
        if self.permission_broker is not None:
            await self.permission_broker.cancel_child_generation(
                self.handle.employee.employee_id,
                self.handle.binding.binding_generation,
                self.handle.child_generation,
                reason,
                deadline,
            )
        if self.terminal_service is not None:
            if deadline is None:
                deadline = (
                    asyncio.get_running_loop().time()
                    + ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS
                )
            unfinished = await self.terminal_service.cleanup_child_generation(
                self.handle.employee.employee_id,
                self.handle.binding.acp_session_id,
                self.handle.child_generation,
                deadline,
            )
            if unfinished:
                raise _ConversationReverseServiceCleanupError(
                    "terminal generation cleanup was unfinished"
                )

    async def _observe_session_notification(
        self, notification: SessionNotification
    ) -> None:
        active = self.active
        if active is None or active.cancellation_cause is not None:
            return
        strategy = self.handle.definition.turn_strategy
        observation = strategy.observe_compaction(self.handle.binding, notification)
        if observation is None:
            return
        if observation.state != "compacting" or observation.trigger != "automatic":
            raise ConversationTurnBrokerError(
                "backend returned an invalid compaction observation"
            )
        identity = observation.boundary_id
        if identity in self.observed_compactions:
            return
        self.observed_compactions.add(identity)
        if (
            self.boundaries
            and self.boundaries[0].trigger == "explicit"
            and self.boundaries[0].observation_identity is None
        ):
            self.boundaries[0].observation_identity = identity
            return
        boundary = _Boundary(
            boundary_id=self.boundary_id_factory(),
            trigger="automatic",
            observation_identity=identity,
        )
        self.boundaries.append(boundary)
        await self.publisher.publish_compaction(
            self.handle.employee,
            self.handle.binding,
            ContextCompaction(
                boundary_id=boundary.boundary_id,
                state="compacting",
                trigger="automatic",
            ),
        )
        await self.activity("compacting", "Compacting conversation context")

    async def _reject_queue(self, reason: str) -> None:
        while self.queue:
            queued = self.queue.popleft()
            self.queued_runtime_handles.pop(queued.client_message_id, None)
            await self.receipt(queued.client_message_id, "queue", "rejected", reason=reason)
        await self.publisher.publish_queue_snapshot(self.handle.employee, self.handle.binding, ())

    async def _fail_boundaries(self, reason: str) -> None:
        boundaries, self.boundaries = self.boundaries, []
        for boundary in boundaries:
            await self.publisher.publish_compaction(
                self.handle.employee,
                self.handle.binding,
                ContextCompaction(
                    boundary_id=boundary.boundary_id,
                    state="failed",
                    trigger=boundary.trigger,
                    reason=reason,
                ),
            )

    def _enqueue_background(self, operation: Callable[[_Actor], Awaitable[Any]]) -> None:
        runner = self.runner
        if (
            self.lifecycle in {"closed", "failed"}
            or not self.accepting_commands
            or runner is None
            or runner.done()
        ):
            return
        self.commands.put_nowait(_Command(operation=operation, future=None))

    def _matches_runtime(self, handle: ConversationRuntimeHandle) -> bool:
        return (
            self.handle.record_identity is handle.record_identity
            and self.handle.child_generation == handle.child_generation
            and self.handle.employee == handle.employee
            and self.handle.binding == handle.binding
            and self.handle.child is handle.child
            and self.handle.definition is handle.definition
        )

    @staticmethod
    def _is_explicit_compaction(prompt: PromptRequest) -> bool:
        if len(prompt.prompt) != 1:
            return False
        block = prompt.prompt[0]
        return (
            getattr(block, "type", None) == "text"
            and getattr(block, "text", "").strip() == "/compact"
        )


class ConversationTurnBroker:
    def __init__(
        self,
        runtime: ConversationEmployeeRuntimePort,
        publisher: ConversationRuntimeEventPublisher,
        *,
        integer_now: Callable[[], int],
        boundary_id_factory: Callable[[], str] | None = None,
        cancel_timeout_seconds: float = ACP_ACTIVE_PROMPT_CANCEL_TIMEOUT_SECONDS,
        capture_timeout_seconds: float = ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
        requested_cancel_recovery_timeout_seconds: float = (
            ACP_CONVERSATION_SERVICE_SHUTDOWN_TIMEOUT_SECONDS
        ),
        permission_broker: ConversationPermissionBroker | None = None,
        terminal_service: ScopedAcpTerminalService | None = None,
    ) -> None:
        if min(
            cancel_timeout_seconds,
            capture_timeout_seconds,
            requested_cancel_recovery_timeout_seconds,
        ) <= 0:
            raise ValueError("conversation broker timeouts must be positive")
        self._runtime = runtime
        self._publisher = publisher
        self._integer_now = integer_now
        self._boundary_counter = 0
        self._boundary_id_factory = boundary_id_factory or self._next_boundary_id
        self._cancel_timeout_seconds = cancel_timeout_seconds
        self._capture_timeout_seconds = capture_timeout_seconds
        self._requested_cancel_recovery_timeout_seconds = (
            requested_cancel_recovery_timeout_seconds
        )
        self._permission_broker = permission_broker
        self._terminal_service = terminal_service
        if permission_broker is not None:
            permission_broker.set_publication_failure_callback(
                self.permission_publication_failed
            )
        if terminal_service is not None:
            set_terminal_failure_callback = getattr(
                terminal_service, "set_publication_failure_callback", None
            )
            if set_terminal_failure_callback is not None:
                set_terminal_failure_callback(self.terminal_publication_failed)
        self._actors: dict[tuple[str, int], _Actor] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._prompt_started_hook: PromptStartedHook | None = None
        self._prompt_settlement_barrier: PromptSettlementBarrier | None = None
        self._compaction_transition_port: ConversationCompactionTransitionPort | None = None
        self._requested_cancel_transition_port: (
            ConversationRequestedCancelRecoveryTransitionPort | None
        ) = None

    def set_prompt_ingress_hooks(
        self,
        *,
        prompt_started: PromptStartedHook,
        settlement_barrier: PromptSettlementBarrier,
    ) -> None:
        if (
            self._prompt_started_hook is not None
            or self._prompt_settlement_barrier is not None
        ):
            raise RuntimeError("prompt ingress hooks are already bound")
        if self._actors:
            raise RuntimeError("prompt ingress hooks must bind before actor creation")
        self._prompt_started_hook = prompt_started
        self._prompt_settlement_barrier = settlement_barrier

    def set_compaction_transition_port(
        self, port: ConversationCompactionTransitionPort
    ) -> None:
        if self._compaction_transition_port is not None:
            raise RuntimeError("compaction transition port is already bound")
        if self._actors:
            raise RuntimeError("compaction transition port must bind before actor creation")
        self._compaction_transition_port = port

    def set_requested_cancel_recovery_transition_port(
        self, port: ConversationRequestedCancelRecoveryTransitionPort
    ) -> None:
        if self._requested_cancel_transition_port is not None:
            raise RuntimeError("requested-cancel transition port is already bound")
        if self._actors:
            raise RuntimeError(
                "requested-cancel transition port must bind before actor creation"
            )
        self._requested_cancel_transition_port = port

    async def deliver(
        self,
        handle: ConversationRuntimeHandle,
        client_message_id: str,
        choice: TurnDeliveryChoice,
        prompt: PromptRequest,
    ) -> TurnDeliveryReceipt:
        actor = await self._actor(handle)

        async def operation(record: _Actor) -> TurnDeliveryReceipt:
            await self._adopt_runtime(record, handle)
            if client_message_id in record.claimed_ids:
                receipt = TurnDeliveryReceipt(
                    client_message_id=client_message_id,
                    choice=choice,
                    state="rejected",
                    reason="Client message ID was already used",
                )
                await record.publisher.publish_delivery_receipt(
                    record.handle.employee, record.handle.binding, receipt
                )
                return receipt
            record.claimed_ids.add(client_message_id)
            if prompt.session_id != record.handle.binding.acp_session_id:
                receipt = TurnDeliveryReceipt(
                    client_message_id=client_message_id,
                    choice=choice,
                    state="rejected",
                    reason="Prompt session does not match the live conversation",
                )
                await record.publisher.publish_delivery_receipt(
                    record.handle.employee, record.handle.binding, receipt
                )
                return receipt
            if record.lifecycle != "open":
                receipt = TurnDeliveryReceipt(
                    client_message_id=client_message_id,
                    choice=choice,
                    state="rejected",
                    reason="Conversation is not accepting prompts",
                )
                await record.publisher.publish_delivery_receipt(
                    record.handle.employee, record.handle.binding, receipt
                )
                return receipt
            if choice == "normal":
                if record.active is not None or record.capture_task is not None:
                    return await self._reject(
                        record, client_message_id, choice, "A prompt is already active"
                    )
                await record._start(
                    _QueuedSubmission(client_message_id, choice, prompt, handle),
                    publish_accepted=True,
                )
                return TurnDeliveryReceipt(
                    client_message_id=client_message_id, choice=choice, state="started"
                )
            if choice == "queue":
                if record.active is None:
                    return await self._reject(
                        record,
                        client_message_id,
                        choice,
                        "Queue is available only during an active prompt",
                    )
                record.enqueue_sequence += 1
                queued = QueuedPrompt(
                    client_message_id=client_message_id,
                    prompt=prompt,
                    enqueue_sequence=record.enqueue_sequence,
                    enqueued_at=record.integer_now(),
                )
                record.queue.append(queued)
                record.queued_runtime_handles[client_message_id] = handle
                position = len(record.queue)
                await record.receipt(client_message_id, choice, "queued", queue_position=position)
                await record.publisher.publish_queue_snapshot(
                    record.handle.employee, record.handle.binding, tuple(record.queue)
                )
                return TurnDeliveryReceipt(
                    client_message_id=client_message_id,
                    choice=choice,
                    state="queued",
                    queue_position=position,
                )
            if choice == "send_now":
                if record.active is None or record.active.cancellation_cause is not None:
                    return await self._reject(
                        record, client_message_id, choice, "Send Now requires a running prompt"
                    )
                await record.receipt(client_message_id, choice, "accepted")
                await record._begin_cancel(
                    "send_now",
                    _QueuedSubmission(client_message_id, choice, prompt, handle),
                )
                return TurnDeliveryReceipt(
                    client_message_id=client_message_id, choice=choice, state="accepted"
                )
            if choice == "steer":
                if (
                    record.active is None
                    or record.active.cancellation_cause is not None
                    or not record.handle.definition.turn_capabilities.supports_steer
                ):
                    return await self._reject(
                        record, client_message_id, choice, "Steer is unavailable"
                    )
                lease = await record.runtime.acquire_runtime_lease(record.handle)
                strategy = record.runtime.strategy_for_lease(lease)
                task = asyncio.create_task(
                    strategy.steer(record.handle.binding, prompt, client_message_id),
                    name=(
                        f"panels.acp.steer.{record.handle.employee.employee_id}."
                        f"{record.handle.child_generation}.{client_message_id}"
                    ),
                )
                record.strategy_tasks.add(task)
                task.add_done_callback(
                    lambda completed: record._enqueue_background(
                        lambda actor: self._settle_steer(actor, client_message_id, completed)
                    )
                )
                return TurnDeliveryReceipt(
                    client_message_id=client_message_id,
                    choice="steer",
                    state="accepted",
                )
            raise AssertionError(choice)

        return cast(TurnDeliveryReceipt, await self._command(actor, operation))

    async def deliver_tracked_normal(
        self,
        handle: ConversationRuntimeHandle,
        client_message_id: str,
        prompt: PromptRequest,
        *,
        before_prompt_started: TrackedTurnHook,
        after_prompt_settled: TrackedTurnHook,
    ) -> TrackedTurnHandle:
        """Start one worker-owned normal prompt and expose its exact terminal epoch."""

        actor = await self._actor(handle)
        completion: asyncio.Future[TrackedTurnResult] = (
            asyncio.get_running_loop().create_future()
        )

        async def operation(record: _Actor) -> TrackedTurnHandle:
            await self._adopt_runtime(record, handle)
            if client_message_id in record.claimed_ids:
                raise ConversationTurnBrokerError("client message ID was already used")
            if prompt.session_id != handle.binding.acp_session_id:
                raise ConversationTurnBrokerError(
                    "prompt session does not match the live conversation"
                )
            if (
                record.lifecycle != "open"
                or record.active is not None
                or record.capture_task is not None
            ):
                raise ConversationTurnBrokerError("a prompt is already active")
            record.claimed_ids.add(client_message_id)
            tracked = await record._start(
                _QueuedSubmission(
                    client_message_id,
                    "normal",
                    prompt,
                    handle,
                    origin="worker",
                    tracked_completion=completion,
                    before_prompt_started=before_prompt_started,
                    after_prompt_settled=after_prompt_settled,
                ),
                publish_accepted=True,
            )
            if tracked is None:
                raise AssertionError("tracked delivery did not allocate a handle")
            return tracked

        return cast(TrackedTurnHandle, await self._command(actor, operation))

    async def attach_state(
        self, handle: ConversationRuntimeHandle
    ) -> ConversationTurnAttachState:
        actor = self._actors.get(
            (handle.employee.employee_id, handle.binding.binding_generation)
        )
        if actor is None:
            return ConversationTurnAttachState(
                "closed" if self._closing else "idle", ()
            )

        async def operation(record: _Actor) -> ConversationTurnAttachState:
            await self._adopt_runtime(record, handle)
            phase: Literal[
                "idle",
                "running",
                "cancelling",
                "capture-finalizing",
                "failed",
                "closed",
            ]
            if record.lifecycle == "failed":
                phase = "failed"
            elif record.lifecycle in {"closing", "closed"}:
                phase = "closed"
            elif record.capture_task is not None:
                phase = "capture-finalizing"
            elif record.active is not None:
                phase = (
                    "cancelling"
                    if record.active.cancellation_cause is not None
                    else "running"
                )
            else:
                phase = "idle"
            return ConversationTurnAttachState(phase, tuple(record.queue))

        return cast(ConversationTurnAttachState, await self._command(actor, operation))

    async def protocol_update_rejected(
        self,
        handle: ConversationRuntimeHandle,
        rejection: ProtocolUpdateRejectedPayload,
    ) -> None:
        del rejection
        actor = self._actors.get(
            (handle.employee.employee_id, handle.binding.binding_generation)
        )
        if actor is None:
            return

        async def operation(record: _Actor) -> None:
            await self._adopt_runtime(record, handle)
            if record.active is not None or record.capture_task is not None:
                await record._fail_generation(
                    "Agent sent an unsupported update",
                    failure_provenance="backend",
                )

        await self._command(actor, operation)

    async def cancel(
        self,
        handle: ConversationRuntimeHandle,
        queued_client_message_id: str | None = None,
    ) -> None:
        actor = await self._actor(handle)

        async def operation(record: _Actor) -> None:
            await self._adopt_runtime(record, handle)
            if queued_client_message_id is not None:
                for index, queued in enumerate(record.queue):
                    if queued.client_message_id == queued_client_message_id:
                        del record.queue[index]
                        record.queued_runtime_handles.pop(
                            queued.client_message_id, None
                        )
                        await record.receipt(
                            queued.client_message_id,
                            "queue",
                            "interrupted",
                            reason="Queued prompt cancelled",
                        )
                        await record.publisher.publish_queue_snapshot(
                            record.handle.employee,
                            record.handle.binding,
                            tuple(record.queue),
                        )
                        return
                await record.receipt(
                    queued_client_message_id,
                    "queue",
                    "rejected",
                    reason="Queued prompt was not found",
                )
                return
            if record.active is None or record.active.cancellation_cause is not None:
                raise ConversationTurnBrokerError("no cancellable active prompt")
            await record._begin_cancel("user", None)

        await self._command(actor, operation)

    async def observe_session_notification(
        self,
        handle: ConversationRuntimeHandle,
        notification: SessionNotification,
    ) -> None:
        actor = await self._actor(handle)

        async def operation(record: _Actor) -> None:
            await self._adopt_runtime(record, handle)
            await record._observe_session_notification(notification)

        await self._command(actor, operation)

    async def request_permission(
        self,
        handle: ConversationRuntimeHandle,
        request: RequestPermissionRequest,
    ) -> RequestPermissionResponse:
        """Admit one reverse permission call against the exact active epoch."""

        cancelled = RequestPermissionResponse(
            outcome=DeniedOutcome(outcome="cancelled")
        )
        if self._permission_broker is None:
            return cancelled
        try:
            actor = await self._actor(handle)
        except ConversationTurnBrokerError:
            return cancelled

        async def operation(record: _Actor) -> tuple[int, Literal["browser", "worker"]] | None:
            try:
                await self._adopt_runtime(record, handle)
            except ConversationTurnBrokerError:
                return None
            active = record.active
            if (
                record.lifecycle != "open"
                or active is None
                or active.cancellation_cause is not None
                or record.handle.record_identity is not handle.record_identity
                or record.handle.child_generation != handle.child_generation
                or record.handle.binding != handle.binding
                or request.session_id != handle.binding.acp_session_id
                or not handle.definition.reverse_service_capabilities.permission
            ):
                return None
            try:
                await record.runtime.acquire_runtime_lease(handle)
            except Exception:
                return None
            return active.epoch, active.origin

        try:
            prompt_context = cast(
                tuple[int, Literal["browser", "worker"]] | None,
                await self._command(actor, operation),
            )
        except ConversationTurnBrokerError:
            return cancelled
        if prompt_context is None:
            return cancelled
        epoch, origin = prompt_context
        return await asyncio.shield(
            self._permission_broker.request_permission(
                handle.employee,
                handle.binding,
                handle.child_generation,
                handle.record_identity,
                epoch,
                request,
                permission_declared=True,
                origin=origin,
            )
        )

    async def child_died(
        self,
        employee_id: str,
        binding_generation: int,
        child_generation: int,
        error: BaseException | None,
    ) -> None:
        actor = self._actors.get((employee_id, binding_generation))
        if actor is None:
            return

        async def operation(record: _Actor) -> None:
            if (
                record.lifecycle in {"closing", "closed"}
                or record.handle.child_generation != child_generation
            ):
                return
            await record._fail_generation(
                _backend_failure_reason(error), failure_provenance="backend"
            )

        try:
            await self._command(actor, operation)
        except ConversationTurnBrokerError:
            return

    async def permission_publication_failed(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        child_generation: int,
        record_identity: object,
        prompt_epoch: int,
        error: Exception,
    ) -> None:
        del error
        actor = self._actors.get(
            (employee.employee_id, binding.binding_generation)
        )
        if actor is None:
            return

        async def operation(record: _Actor) -> None:
            active = record.active
            if (
                record.handle.record_identity is not record_identity
                or record.handle.child_generation != child_generation
                or record.handle.binding != binding
                or active is None
                or active.epoch != prompt_epoch
            ):
                return
            await record._fail_generation("Permission publication failed")

        try:
            await self._command(actor, operation)
        except ConversationTurnBrokerError:
            return

    async def terminal_publication_failed(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        child_generation: int,
        error: Exception,
    ) -> None:
        del error
        actor = self._actors.get(
            (employee.employee_id, binding.binding_generation)
        )
        if actor is None:
            return

        async def operation(record: _Actor) -> None:
            if (
                record.handle.child_generation != child_generation
                or record.handle.binding != binding
            ):
                return
            await record._fail_generation("Terminal publication failed")

        try:
            await self._command(actor, operation)
        except ConversationTurnBrokerError:
            return

    async def bind_runtime(self, handle: ConversationRuntimeHandle) -> None:
        actor = self._actors.get((handle.employee.employee_id, handle.binding.binding_generation))
        if actor is None:
            await self._actor(handle)
            return

        async def operation(record: _Actor) -> None:
            await self._adopt_runtime(record, handle)

        await self._command(actor, operation)

    async def prepare_new_conversation(
        self, handle: ConversationRuntimeHandle, deadline: float
    ) -> None:
        actor = await self._actor(handle)
        await self._close_actor(actor, "new_conversation", deadline)

    async def shutdown(self, deadline: float) -> None:
        async with self._lock:
            task = self._shutdown_task
            if task is None:
                self._closing = True
                actors = tuple(self._actors.values())
                task = asyncio.create_task(
                    self._shutdown_owned(actors, deadline),
                    name="panels.acp.turn-broker.shutdown",
                )
                self._shutdown_task = task
        await asyncio.shield(task)

    async def _shutdown_owned(
        self, actors: tuple[_Actor, ...], deadline: float
    ) -> None:
        actor_tasks = {
            asyncio.create_task(
                self._close_actor(actor, "shutdown", deadline),
                name=(
                    f"panels.acp.turn-broker.close."
                    f"{actor.handle.employee.employee_id}"
                ),
            ): actor
            for actor in actors
        }
        owner_tasks: set[asyncio.Task[Any]] = set()
        if self._permission_broker is not None:
            owner_tasks.add(
                asyncio.create_task(self._permission_broker.shutdown(deadline))
            )
        if self._terminal_service is not None:
            owner_tasks.add(
                asyncio.create_task(self._terminal_service.shutdown(deadline))
            )
        tasks: set[asyncio.Task[Any]] = set(actor_tasks) | owner_tasks
        if not tasks:
            return
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, pending = await asyncio.wait(tasks, timeout=remaining)
        unfinished_employees = {
            actor_tasks[task].handle.employee.employee_id
            for task in pending
            if task in actor_tasks
        }
        failures: list[BaseException] = []
        for completed in done:
            if completed.cancelled():
                continue
            try:
                result = completed.result()
                if completed in owner_tasks and isinstance(result, tuple) and result:
                    failures.append(
                        ConversationTurnBrokerError(
                            "conversation reverse-service cleanup was unfinished"
                        )
                    )
            except Exception as error:
                failures.append(error)
                actor = actor_tasks.get(completed)
                if actor is not None:
                    unfinished_employees.add(actor.handle.employee.employee_id)
        for pending_task in pending:
            pending_task.cancel()
        if any(task in owner_tasks for task in pending):
            unfinished_employees.update(
                actor.handle.employee.employee_id for actor in actors
            )
        for actor in actors:
            runner = actor.runner
            if actor.lifecycle != "closed" or (runner is not None and not runner.done()):
                unfinished_employees.add(actor.handle.employee.employee_id)
                self._force_dispose_actor(actor)
        self._actors.clear()
        if pending or failures:
            if not unfinished_employees:
                unfinished_employees.update(
                    actor.handle.employee.employee_id for actor in actors
                )
            raise ConversationTurnBrokerShutdownError(
                tuple(sorted(unfinished_employees))
            )

    async def _close_actor(
        self, actor: _Actor, cause: Literal["new_conversation", "shutdown"], deadline: float
    ) -> None:
        task = asyncio.create_task(
            self._close_actor_steps(actor, cause, deadline),
            name=(
                f"panels.acp.turn-broker.close-steps."
                f"{actor.handle.employee.employee_id}"
            ),
        )
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _pending = await asyncio.wait({task}, timeout=remaining)
        if not done:
            task.cancel()
            task.add_done_callback(_Actor._consume_late_deadline_result)
            self._force_dispose_actor(actor)
            raise TimeoutError
        try:
            task.result()
        except BaseException:
            self._force_dispose_actor(actor)
            raise

    async def _close_actor_steps(
        self, actor: _Actor, cause: Literal["new_conversation", "shutdown"], deadline: float
    ) -> None:
        if actor.lifecycle == "closed":
            return

        async def operation(record: _Actor) -> None:
            if record.lifecycle == "closed":
                return
            record.lifecycle = "closing"
            if record.active is not None:
                if record.active.cancellation_cause is None:
                    await record._begin_cancel(cause, None, close_deadline=deadline)
            await record._reject_queue("Conversation is closing")
            await record._fail_boundaries("Conversation is closing")

        await self._command(actor, operation)
        active = actor.active
        if active is not None:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            done, _pending = await asyncio.wait(
                {active.task},
                timeout=min(actor.cancel_timeout_seconds, remaining / 2),
            )
            if done and not active.task.cancelled():
                # The generation/epoch callback owns prompt failure. Closing
                # still has to finish against the caller's absolute deadline.
                try:
                    active.task.result()
                except Exception:
                    pass

                cancellation_tasks = {
                    task
                    for task in (
                        active.cancel_task,
                        active.permission_cancel_task,
                    )
                    if task is not None
                }
                if cancellation_tasks:
                    remaining = max(
                        0.0, deadline - asyncio.get_running_loop().time()
                    )
                    await asyncio.wait(
                        cancellation_tasks,
                        timeout=min(actor.cancel_timeout_seconds, remaining / 2),
                    )

                async def settle_completed_prompt(record: _Actor) -> None:
                    await record._settle_prompt(active.epoch, active.task)

                await self._command(actor, settle_completed_prompt)

        async def finish(record: _Actor) -> None:
            if record.close_retirement_failure is not None:
                raise record.close_retirement_failure
            if record.active is not None:
                active = record.active
                await record._interrupt_active(
                    active, "Conversation closed while prompt was active"
                )
                record._settle_tracked(
                    active.tracked_handle,
                    active.tracked_terminal_hook,
                    TrackedTurnResult("interrupted"),
                )
                if active.timeout_task is not None:
                    active.timeout_task.cancel()
                if active.cancel_task is not None:
                    active.cancel_task.cancel()
                if active.permission_cancel_task is not None:
                    active.permission_cancel_task.cancel()
                active.task.cancel()
                record.active = None
            if record.capture_task is not None:
                record.capture_task.cancel()
                record.capture_task = None
            record._settle_tracked(
                record.capture_tracked_handle,
                record.capture_tracked_terminal_hook,
                TrackedTurnResult("interrupted"),
            )
            record.capture_tracked_handle = None
            record.capture_tracked_terminal_hook = None
            record.capture_prompt_response = None
            record.capture_strategy = None
            for strategy_task in record.strategy_tasks:
                if not strategy_task.done():
                    strategy_task.cancel()
            record.strategy_tasks.clear()
            if record.owner_cleanup_tasks:
                for cleanup_task in record.owner_cleanup_tasks:
                    cleanup_task.cancel()
                record.owner_cleanup_tasks.clear()
            await record._settle_generation_services(
                "Conversation is closing", deadline
            )
            record.lifecycle = "closed"

        await self._command(actor, finish)
        if cause == "new_conversation":
            if self._permission_broker is not None:
                await self._permission_broker.cancel_binding(
                    actor.handle.employee.employee_id,
                    actor.handle.binding.binding_generation,
                    "A new conversation is starting",
                    deadline,
                )
            if self._terminal_service is not None:
                unfinished = await self._terminal_service.prepare_new_conversation(
                    actor.handle.binding, deadline
                )
                if unfinished:
                    raise ConversationTurnBrokerError(
                        "terminal cleanup exceeded the new-conversation deadline"
                    )
        runner = actor.runner
        if runner is not None:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            runner_done, _runner_pending = await asyncio.wait(
                {runner}, timeout=remaining
            )
            if not runner_done:
                self._force_dispose_actor(actor)
                raise TimeoutError

    async def _settle_steer(
        self,
        actor: _Actor,
        client_message_id: str,
        task: asyncio.Task[TurnDeliveryReceipt],
    ) -> None:
        actor.strategy_tasks.discard(task)
        try:
            receipt = task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            receipt = TurnDeliveryReceipt(
                client_message_id=client_message_id,
                choice="steer",
                state="rejected",
                reason="Steer delivery failed",
            )
        await actor.publisher.publish_delivery_receipt(
            actor.handle.employee, actor.handle.binding, receipt
        )

    async def _reject(
        self,
        actor: _Actor,
        client_message_id: str,
        choice: TurnDeliveryChoice,
        reason: str,
    ) -> TurnDeliveryReceipt:
        receipt = TurnDeliveryReceipt(
            client_message_id=client_message_id,
            choice=choice,
            state="rejected",
            reason=reason,
        )
        await actor.publisher.publish_delivery_receipt(
            actor.handle.employee, actor.handle.binding, receipt
        )
        return receipt

    async def _adopt_runtime(
        self, actor: _Actor, handle: ConversationRuntimeHandle
    ) -> None:
        if actor._matches_runtime(handle):
            return
        current = actor.handle
        if (
            current.employee != handle.employee
            or current.binding != handle.binding
            or handle.child_generation <= current.child_generation
        ):
            raise ConversationTurnBrokerError("stale conversation runtime handle")
        if actor.lifecycle in {"closing", "closed"}:
            raise ConversationTurnBrokerError("conversation actor is closed")
        if (
            actor.active is not None
            or actor.capture_task is not None
            or actor.strategy_tasks
            or actor.boundaries
            or actor.queue
        ):
            raise ConversationTurnBrokerError(
                "conversation runtime cannot change while work is active"
            )
        actor.handle = handle
        if actor.lifecycle == "failed":
            actor.lifecycle = "open"
            await actor.activity("idle", "Employee is ready")

    @staticmethod
    def _force_dispose_actor(actor: _Actor) -> None:
        actor.accepting_commands = False
        actor.lifecycle = "closed"
        current = actor.current_command
        if current is not None and current.future is not None and not current.future.done():
            current.future.cancel()
        while not actor.commands.empty():
            command = actor.commands.get_nowait()
            if command.future is not None and not command.future.done():
                command.future.cancel()
        tasks: list[asyncio.Task[Any]] = []
        active, actor.active = actor.active, None
        if active is not None:
            actor._settle_tracked(
                active.tracked_handle,
                active.tracked_terminal_hook,
                TrackedTurnResult("errored", error="Conversation was force-closed"),
            )
            tasks.extend(
                task
                for task in (
                    active.task,
                    active.cancel_task,
                    active.timeout_task,
                    active.permission_cancel_task,
                )
                if task is not None
            )
        if actor.capture_task is not None:
            tasks.append(actor.capture_task)
            actor.capture_task = None
        actor._settle_tracked(
            actor.capture_tracked_handle,
            actor.capture_tracked_terminal_hook,
            TrackedTurnResult("errored", error="Conversation was force-closed"),
        )
        actor.capture_tracked_handle = None
        actor.capture_tracked_terminal_hook = None
        actor.capture_prompt_response = None
        tasks.extend(actor.strategy_tasks)
        actor.strategy_tasks.clear()
        tasks.extend(actor.owner_cleanup_tasks)
        actor.owner_cleanup_tasks.clear()
        runner, actor.runner = actor.runner, None
        if runner is not None:
            tasks.append(runner)
        for task in tasks:
            if not task.done():
                task.cancel()
        actor.capture_strategy = None
        actor.queue.clear()
        actor.queued_runtime_handles.clear()
        actor.boundaries.clear()

    async def _actor(self, handle: ConversationRuntimeHandle) -> _Actor:
        key = (handle.employee.employee_id, handle.binding.binding_generation)
        async with self._lock:
            if self._closing:
                raise ConversationTurnBrokerError("conversation turn broker is closing")
            actor = self._actors.get(key)
            if actor is None:
                actor = _Actor(
                    runtime=self._runtime,
                    publisher=self._publisher,
                    handle=handle,
                    integer_now=self._integer_now,
                    boundary_id_factory=self._boundary_id_factory,
                    cancel_timeout_seconds=self._cancel_timeout_seconds,
                    permission_broker=self._permission_broker,
                    terminal_service=self._terminal_service,
                    commands=asyncio.Queue(),
                    prompt_started_hook=self._prompt_started_hook,
                    prompt_settlement_barrier=self._prompt_settlement_barrier,
                    compaction_transition_port=self._compaction_transition_port,
                    requested_cancel_transition_port=(
                        self._requested_cancel_transition_port
                    ),
                    actor_rekey=self._rekey_actor,
                    capture_timeout_seconds=self._capture_timeout_seconds,
                    requested_cancel_recovery_timeout_seconds=(
                        self._requested_cancel_recovery_timeout_seconds
                    ),
                )
                actor.runner = asyncio.create_task(
                    actor.run(),
                    name=f"panels.acp.turn-broker.{handle.employee.employee_id}.{handle.binding.binding_generation}",
                )
                self._actors[key] = actor
            elif actor.lifecycle == "closed":
                raise ConversationTurnBrokerError("conversation actor is closed")
            return actor

    async def _rekey_actor(
        self,
        actor: _Actor,
        previous_handle: ConversationRuntimeHandle,
        replacement_handle: ConversationRuntimeHandle,
    ) -> None:
        previous_key = (
            previous_handle.employee.employee_id,
            previous_handle.binding.binding_generation,
        )
        replacement_key = (
            replacement_handle.employee.employee_id,
            replacement_handle.binding.binding_generation,
        )
        async with self._lock:
            if self._actors.get(previous_key) is not actor:
                raise ConversationTurnBrokerError(
                    "compaction actor lost its original broker key"
                )
            conflict = self._actors.get(replacement_key)
            if conflict is not None and conflict is not actor:
                raise ConversationTurnBrokerError(
                    "compaction replacement conflicts with a live actor"
                )
            self._actors.pop(previous_key, None)
            self._actors[replacement_key] = actor

    @staticmethod
    async def _command(actor: _Actor, operation: Callable[[_Actor], Awaitable[Any]]) -> Any:
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        async with actor.command_admission_lock:
            runner = actor.runner
            if (
                actor.lifecycle == "closed"
                or not actor.accepting_commands
                or runner is None
                or runner.done()
            ):
                raise ConversationTurnBrokerError(
                    "conversation actor is not accepting commands"
                )
            actor.commands.put_nowait(_Command(operation=operation, future=future))
        return await asyncio.shield(future)

    def _next_boundary_id(self) -> str:
        self._boundary_counter += 1
        return f"compaction-{self._boundary_counter}"
