"""Narrow exact-generation ports from ACP-02 into the ACP-01 runtime."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from acp.schema import (
    CancelNotification,
    CreateTerminalRequest,
    CreateTerminalResponse,
    KillTerminalRequest,
    KillTerminalResponse,
    PromptRequest,
    PromptResponse,
    ReadTextFileRequest,
    ReadTextFileResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    SessionNotification,
    TerminalOutputRequest,
    TerminalOutputResponse,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
)

from .backend_contracts import AcpEmployeeChild, AgentBackendDefinition, BackendTurnStrategy
from .configuration import ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS
from .contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationSessionBinding,
    QueuedPrompt,
    TurnDeliveryReceipt,
)
from .wire_contracts import HumanEcho, ProtocolUpdateRejectedPayload


class ConversationRuntimeUnavailable(RuntimeError):
    """The exact child generation captured for an operation is no longer live."""


class ConversationRuntimeGenerationFatal(ConversationRuntimeUnavailable):
    """The exact runtime generation was invalidated and cannot continue."""


class ConversationWaitDeadlineExpired(TimeoutError):
    """Internal signal that Panels' own absolute-deadline wait timer won."""


type ConversationCompactionCapturePhase = Literal[
    "hub admission",
    "backend observation",
    "fork",
    "private fork load",
    "normalization",
    "durable CAS/resolve",
    "winner adoption",
    "actor rekey",
    "browser commit",
]
type ConversationCompactionDurableBindingDisposition = Literal[
    "stayed", "committed", "unresolved"
]


def _describe_compaction_error(error: BaseException) -> str:
    error_type = type(error).__name__
    message = str(error).strip()
    return f"{error_type}: {message}" if message else error_type


class ConversationCompactionCaptureDeadlineExpired(ConversationRuntimeUnavailable):
    """A single compaction transaction exhausted its actor-owned budget."""

    def __init__(
        self,
        *,
        phase: ConversationCompactionCapturePhase,
        capture_timeout_seconds: float,
        durable_binding_disposition: ConversationCompactionDurableBindingDisposition,
        original_binding_generation: int,
        generation_fatal: bool,
        recovery_phase: ConversationCompactionCapturePhase | None = None,
        recovery_error: BaseException | None = None,
    ) -> None:
        if (recovery_phase is None) != (recovery_error is None):
            raise ValueError(
                "recovery_phase and recovery_error must either both be set or both be absent"
            )
        self.phase = phase
        self.capture_timeout_seconds = capture_timeout_seconds
        self.durable_binding_disposition = durable_binding_disposition
        self.original_binding_generation = original_binding_generation
        self.generation_fatal = generation_fatal
        self.recovery_phase = recovery_phase
        self.recovery_error = recovery_error
        super().__init__(self._reason())

    def as_generation_fatal(self) -> ConversationCompactionCaptureDeadlineExpired:
        if self.generation_fatal:
            return self
        return ConversationCompactionCaptureDeadlineExpired(
            phase=self.phase,
            capture_timeout_seconds=self.capture_timeout_seconds,
            durable_binding_disposition=self.durable_binding_disposition,
            original_binding_generation=self.original_binding_generation,
            generation_fatal=True,
            recovery_phase=self.recovery_phase,
            recovery_error=self.recovery_error,
        )

    def with_recovery_failure(
        self,
        phase: ConversationCompactionCapturePhase,
        error: BaseException,
    ) -> ConversationCompactionCaptureDeadlineExpired:
        return ConversationCompactionCaptureDeadlineExpired(
            phase=self.phase,
            capture_timeout_seconds=self.capture_timeout_seconds,
            durable_binding_disposition=self.durable_binding_disposition,
            original_binding_generation=self.original_binding_generation,
            generation_fatal=True,
            recovery_phase=phase,
            recovery_error=error,
        )

    def _reason(self) -> str:
        budget = f"{self.capture_timeout_seconds:g}"
        successor_generation = self.original_binding_generation + 1
        if self.durable_binding_disposition == "stayed":
            disposition = (
                "durable binding stayed on generation "
                f"{self.original_binding_generation}"
            )
        elif self.durable_binding_disposition == "committed":
            disposition = f"durable binding committed generation {successor_generation}"
        else:
            disposition = (
                "durable binding disposition was unresolved at deadline between "
                f"generation {self.original_binding_generation} and generation "
                f"{successor_generation}; fresh attach must resolve the authoritative "
                "database binding"
            )
        runtime_disposition = (
            "; the uncertain runtime child was invalidated; fresh attach will resolve "
            "and load the authoritative binding"
            if self.generation_fatal
            else ""
        )
        reason = (
            "Conversation compaction capture exceeded its configured "
            f"{budget}-second budget during {self.phase}; {disposition}"
            f"{runtime_disposition}"
        )
        if self.recovery_phase is not None and self.recovery_error is not None:
            reason += (
                f"; required {self.recovery_phase} also failed: "
                f"{_describe_compaction_error(self.recovery_error)}"
            )
        return reason


class ConversationCompactionCaptureFailed(ConversationRuntimeUnavailable):
    """An immediate compaction failure with its exact phase and concrete cause."""

    def __init__(
        self,
        *,
        phase: ConversationCompactionCapturePhase,
        primary_error: BaseException,
        generation_fatal: bool,
        recovery_phase: ConversationCompactionCapturePhase | None = None,
        recovery_error: BaseException | None = None,
    ) -> None:
        if (recovery_phase is None) != (recovery_error is None):
            raise ValueError(
                "recovery_phase and recovery_error must either both be set or both be absent"
            )
        self.phase = phase
        self.primary_error = primary_error
        self.generation_fatal = generation_fatal
        self.recovery_phase = recovery_phase
        self.recovery_error = recovery_error
        super().__init__(self._reason())

    @staticmethod
    def describe_error(error: BaseException) -> str:
        return _describe_compaction_error(error)

    def with_recovery_failure(
        self,
        phase: ConversationCompactionCapturePhase,
        error: BaseException,
    ) -> ConversationCompactionCaptureFailed:
        return ConversationCompactionCaptureFailed(
            phase=self.phase,
            primary_error=self.primary_error,
            generation_fatal=True,
            recovery_phase=phase,
            recovery_error=error,
        )

    def _reason(self) -> str:
        reason = (
            f"Conversation compaction capture failed during {self.phase}: "
            f"{self.describe_error(self.primary_error)}"
        )
        if self.recovery_phase is not None and self.recovery_error is not None:
            reason += (
                f"; required {self.recovery_phase} also failed: "
                f"{self.describe_error(self.recovery_error)}"
            )
        return reason


@dataclass(frozen=True, slots=True)
class ConversationRuntimeHandle:
    employee: ConversationEmployee
    binding: ConversationSessionBinding
    child_generation: int
    child: AcpEmployeeChild
    definition: AgentBackendDefinition
    record_identity: object


@dataclass(frozen=True, slots=True)
class ConversationRuntimeLease:
    handle: ConversationRuntimeHandle

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        if not self.handle.child.alive:
            raise ConversationRuntimeUnavailable("ACP child generation is no longer alive")
        return await self.handle.child.prompt(request)

    async def cancel(self, notification: CancelNotification) -> None:
        if not self.handle.child.alive:
            raise ConversationRuntimeUnavailable("ACP child generation is no longer alive")
        await self.handle.child.cancel(notification)


@dataclass(frozen=True, slots=True)
class PreparedCompactionCapture:
    transaction_identity: object
    original_handle: ConversationRuntimeHandle
    candidate_binding: ConversationSessionBinding
    captured_replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...]
    expected_compaction_boundaries: tuple[
        ConversationCompactionBoundaryProvenance, ...
    ]
    candidate_compaction_boundaries: tuple[
        ConversationCompactionBoundaryProvenance, ...
    ]
    deadline: float
    capture_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class CompactionCaptureTransition:
    replacement_handle: ConversationRuntimeHandle
    replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...]
    fork_won: bool


@dataclass(frozen=True, slots=True)
class CompactionTransitionToken:
    transaction_identity: object
    original_handle: ConversationRuntimeHandle
    deadline: float


@dataclass(frozen=True, slots=True)
class RequestedCancelRuntimeReplacement:
    replacement_handle: ConversationRuntimeHandle
    replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...]


@dataclass(frozen=True, slots=True)
class RequestedCancelRecoveryTransitionToken:
    transaction_identity: object
    original_handle: ConversationRuntimeHandle
    deadline: float


class ConversationRequestedCancelRecoveryTransitionPort(Protocol):
    async def begin_requested_cancel_recovery_transition(
        self, handle: ConversationRuntimeHandle, deadline: float
    ) -> RequestedCancelRecoveryTransitionToken: ...

    async def resume_requested_cancelled_runtime(
        self, token: RequestedCancelRecoveryTransitionToken
    ) -> None: ...

    async def commit_requested_cancel_recovery_transition(
        self,
        token: RequestedCancelRecoveryTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        queued_prompts: tuple[QueuedPrompt, ...],
        send_now_successor_human_echo: HumanEcho | None,
    ) -> None: ...

    async def fail_requested_cancel_recovery_transition(
        self, token: RequestedCancelRecoveryTransitionToken, reason: str
    ) -> None: ...


class ConversationCompactionTransitionPort(Protocol):
    async def begin_compaction_transition(
        self, handle: ConversationRuntimeHandle, deadline: float
    ) -> CompactionTransitionToken: ...

    async def commit_compaction_transition(
        self,
        token: CompactionTransitionToken,
        replacement_handle: ConversationRuntimeHandle,
        replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
        queued_prompts: tuple[QueuedPrompt, ...],
    ) -> None: ...

    async def complete_compaction_transition(
        self,
        token: CompactionTransitionToken,
        settled_handle: ConversationRuntimeHandle,
    ) -> None: ...

    async def fail_compaction_transition(
        self, token: CompactionTransitionToken, reason: str
    ) -> None: ...

    async def abort_compaction_transition(
        self, token: CompactionTransitionToken
    ) -> None: ...


class ConversationEmployeeRuntimePort(Protocol):
    async def resolve_runtime_handle(
        self, employee_id: str, binding_generation: int
    ) -> ConversationRuntimeHandle: ...

    async def acquire_runtime_lease(
        self, handle: ConversationRuntimeHandle
    ) -> ConversationRuntimeLease: ...

    async def retire_runtime_lease(
        self, lease: ConversationRuntimeLease, deadline: float
    ) -> None: ...

    async def fail_runtime_handle(
        self, handle: ConversationRuntimeHandle
    ) -> None: ...

    async def replace_runtime_after_requested_cancel(
        self, lease: ConversationRuntimeLease, deadline: float
    ) -> RequestedCancelRuntimeReplacement: ...

    async def prepare_compaction_capture(
        self,
        lease: ConversationRuntimeLease,
        capture_transaction_id: str,
        deadline: float,
        capture_timeout_seconds: float = ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ] = (),
    ) -> PreparedCompactionCapture: ...

    async def commit_compaction_capture(
        self, prepared: PreparedCompactionCapture, deadline: float
    ) -> CompactionCaptureTransition: ...

    async def abort_compaction_capture(
        self, prepared: PreparedCompactionCapture, deadline: float
    ) -> None: ...

    def strategy_for_lease(self, lease: ConversationRuntimeLease) -> BackendTurnStrategy: ...


class AcpFilesystemRuntimePort(Protocol):
    async def read_text_file(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: ReadTextFileRequest,
    ) -> ReadTextFileResponse: ...

    async def write_text_file(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: WriteTextFileRequest,
    ) -> WriteTextFileResponse: ...


class AcpTerminalRuntimePort(Protocol):
    async def create_terminal(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: CreateTerminalRequest,
    ) -> CreateTerminalResponse: ...

    async def terminal_output(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: TerminalOutputRequest,
    ) -> TerminalOutputResponse: ...

    async def wait_for_terminal_exit(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: WaitForTerminalExitRequest,
    ) -> WaitForTerminalExitResponse: ...

    async def kill_terminal(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: KillTerminalRequest,
    ) -> KillTerminalResponse: ...

    async def release_terminal(
        self,
        employee: ConversationEmployee,
        child_generation: int,
        request: ReleaseTerminalRequest,
    ) -> ReleaseTerminalResponse: ...


class GenerationBoundConcurrentPromptPort(Protocol):
    async def prompt(
        self,
        lease: ConversationRuntimeLease,
        binding: ConversationSessionBinding,
        client_message_id: str,
        request: PromptRequest,
    ) -> PromptResponse: ...


class GenerationBoundBackendTurnStrategy:
    """Reject strategy work rather than redirecting it to a replacement child."""

    def __init__(
        self,
        runtime: ConversationEmployeeRuntimePort,
        lease: ConversationRuntimeLease,
        strategy: BackendTurnStrategy,
    ) -> None:
        self._runtime = runtime
        self._lease = lease
        self._strategy = strategy
        self._capture_transition: CompactionCaptureTransition | None = None
        self._capture_number = 0

    async def steer(
        self,
        session_binding: ConversationSessionBinding,
        prompt: PromptRequest,
        client_message_id: str,
    ) -> TurnDeliveryReceipt:
        await self._runtime.acquire_runtime_lease(self._lease.handle)
        exact_steer = getattr(self._strategy, "steer_with_runtime_lease", None)
        if exact_steer is not None:
            return cast(
                TurnDeliveryReceipt,
                await exact_steer(
                    self._lease,
                    session_binding,
                    prompt,
                    client_message_id,
                ),
            )
        return await self._strategy.steer(
            session_binding, prompt, client_message_id
        )

    def observe_compaction(
        self,
        session_binding: ConversationSessionBinding,
        notification: SessionNotification,
    ) -> ContextCompaction | None:
        return self._strategy.observe_compaction(session_binding, notification)

    async def capture_compaction(
        self, session_binding: ConversationSessionBinding
    ) -> ContextCompaction:
        deadline = (
            asyncio.get_running_loop().time()
            + ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS
        )
        return await self.capture_compaction_with_deadline(
            session_binding, deadline
        )

    async def capture_compaction_with_deadline(
        self,
        session_binding: ConversationSessionBinding,
        deadline: float,
        capture_timeout_seconds: float = ACP_CONVERSATION_COMPACTION_CAPTURE_TIMEOUT_SECONDS,
        compacted_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ] = (),
    ) -> ContextCompaction:
        try:
            await self._runtime.acquire_runtime_lease(self._lease.handle)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise ConversationCompactionCaptureFailed(
                phase="hub admission",
                primary_error=error,
                generation_fatal=False,
            ) from error
        capture_in_place = getattr(
            self._strategy, "capture_compaction_in_place", None
        )
        if capture_in_place is not None:
            try:
                result = await self._await_in_place_before_deadline(
                    capture_in_place(session_binding), deadline
                )
            except ConversationWaitDeadlineExpired as error:
                raise ConversationCompactionCaptureDeadlineExpired(
                    phase="backend observation",
                    capture_timeout_seconds=capture_timeout_seconds,
                    durable_binding_disposition="stayed",
                    original_binding_generation=(
                        self._lease.handle.binding.binding_generation
                    ),
                    generation_fatal=False,
                ) from error
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                raise ConversationCompactionCaptureFailed(
                    phase="backend observation",
                    primary_error=error,
                    generation_fatal=False,
                ) from error
            if not isinstance(result, ContextCompaction) or result.state not in {
                "compacted",
                "failed",
            }:
                terminal_error = ConversationRuntimeUnavailable(
                    "backend in-place compaction did not produce a terminal "
                    "ContextCompaction"
                )
                raise ConversationCompactionCaptureFailed(
                    phase="backend observation",
                    primary_error=terminal_error,
                    generation_fatal=False,
                ) from terminal_error
            return result
        normalize_capture = getattr(
            self._strategy, "capture_compaction_from_updates", None
        )
        if normalize_capture is None:
            normalization_error = ConversationRuntimeUnavailable(
                "backend strategy cannot normalize a controlled capture"
            )
            raise ConversationCompactionCaptureFailed(
                phase="normalization",
                primary_error=normalization_error,
                generation_fatal=False,
            ) from normalization_error
        self._capture_number += 1
        transaction_id = (
            f"capture-{self._lease.handle.child_generation}-"
            f"{self._capture_number}-{id(self._lease.handle.record_identity)}"
        )
        prepared = await self._runtime.prepare_compaction_capture(
            self._lease,
            transaction_id,
            deadline,
            capture_timeout_seconds,
            compacted_boundaries,
        )
        capture_phase: ConversationCompactionCapturePhase = "normalization"
        try:
            result = normalize_capture(
                prepared.candidate_binding, prepared.captured_replay
            )
            if inspect.isawaitable(result):
                try:
                    result = await self._await_before_deadline(result, deadline)
                except ConversationWaitDeadlineExpired as error:
                    raise ConversationCompactionCaptureDeadlineExpired(
                        phase="normalization",
                        capture_timeout_seconds=capture_timeout_seconds,
                        durable_binding_disposition="stayed",
                        original_binding_generation=(
                            prepared.original_handle.binding.binding_generation
                        ),
                        generation_fatal=False,
                    ) from error
            elif asyncio.get_running_loop().time() >= deadline:
                raise ConversationCompactionCaptureDeadlineExpired(
                    phase="normalization",
                    capture_timeout_seconds=capture_timeout_seconds,
                    durable_binding_disposition="stayed",
                    original_binding_generation=(
                        prepared.original_handle.binding.binding_generation
                    ),
                    generation_fatal=False,
                )
            normalized = cast(ContextCompaction, result)
            if normalized.state != "compacted":
                raise ConversationRuntimeUnavailable(
                    normalized.reason
                    or "compaction normalization did not produce a completed lifecycle"
                )
            capture_phase = "durable CAS/resolve"
            transition = await self._runtime.commit_compaction_capture(
                prepared, deadline
            )
        except BaseException as capture_error:
            if capture_phase == "durable CAS/resolve":
                if isinstance(
                    capture_error,
                    (
                        asyncio.CancelledError,
                        ConversationCompactionCaptureDeadlineExpired,
                        ConversationCompactionCaptureFailed,
                    ),
                ):
                    raise
                raise ConversationCompactionCaptureFailed(
                    phase=capture_phase,
                    primary_error=capture_error,
                    generation_fatal=True,
                ) from capture_error
            try:
                await self._runtime.abort_compaction_capture(prepared, deadline)
            except BaseException as abort_error:
                if isinstance(capture_error, asyncio.CancelledError):
                    raise capture_error from abort_error
                recovery_phase = (
                    abort_error.phase
                    if isinstance(
                        abort_error,
                        (
                            ConversationCompactionCaptureDeadlineExpired,
                            ConversationCompactionCaptureFailed,
                        ),
                    )
                    else capture_phase
                )
                raise ConversationCompactionCaptureFailed(
                    phase=capture_phase,
                    primary_error=capture_error,
                    recovery_phase=recovery_phase,
                    recovery_error=abort_error,
                    generation_fatal=True,
                ) from abort_error
            if isinstance(capture_error, asyncio.CancelledError):
                raise
            if isinstance(
                capture_error, ConversationCompactionCaptureDeadlineExpired
            ):
                if capture_error.generation_fatal:
                    raise
                raise capture_error.as_generation_fatal() from capture_error
            if isinstance(capture_error, ConversationCompactionCaptureFailed):
                if capture_error.generation_fatal:
                    raise
                raise ConversationCompactionCaptureFailed(
                    phase=capture_error.phase,
                    primary_error=capture_error.primary_error,
                    generation_fatal=True,
                    recovery_phase=capture_error.recovery_phase,
                    recovery_error=capture_error.recovery_error,
                ) from capture_error
            raise ConversationCompactionCaptureFailed(
                phase=capture_phase,
                primary_error=capture_error,
                generation_fatal=True,
            ) from capture_error
        self._capture_transition = transition
        if not transition.fork_won:
            return ContextCompaction(
                boundary_id=normalized.boundary_id,
                state="failed",
                trigger=normalized.trigger,
                reason="Another durable conversation binding won compaction",
            )
        return normalized

    @staticmethod
    async def _await_before_deadline(result: object, deadline: float) -> object:
        if not inspect.isawaitable(result):
            return result
        task = asyncio.ensure_future(result)
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _pending = await asyncio.wait({task}, timeout=remaining)
        if task not in done:
            task.cancel()
            task.add_done_callback(GenerationBoundBackendTurnStrategy._consume_late)
            raise ConversationWaitDeadlineExpired(
                "ACP compaction normalization exceeded its deadline"
            )
        return task.result()

    @staticmethod
    async def _await_in_place_before_deadline(
        result: object, deadline: float
    ) -> object:
        if not inspect.isawaitable(result):
            return result
        task = asyncio.ensure_future(result)
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            done, _pending = await asyncio.wait({task}, timeout=remaining)
        except BaseException:
            if not task.done():
                task.cancel()
                task.add_done_callback(
                    GenerationBoundBackendTurnStrategy._consume_late
                )
            raise
        if task not in done:
            task.cancel()
            task.add_done_callback(GenerationBoundBackendTurnStrategy._consume_late)
            raise ConversationWaitDeadlineExpired(
                "ACP in-place compaction observation exceeded its deadline"
            )
        return task.result()

    @staticmethod
    def _consume_late(task: asyncio.Future[object]) -> None:
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.result()

    def take_capture_transition(self) -> CompactionCaptureTransition | None:
        transition = self._capture_transition
        self._capture_transition = None
        return transition
