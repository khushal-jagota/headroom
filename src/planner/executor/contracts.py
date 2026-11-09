"""Shapes and interfaces between the parts of the mailbox transport (protocol 22).

The wire bodies live in executor/protocol.py. This module holds what the two halves
share beyond the wire: the Mac's command journal schema, the VPS presence record and
health read, the mailbox row states, and the interfaces each half's pieces present to
one another and to the conversation core.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from planner.conversation.backends.contracts import (
    BackendChild,
    RetainedBackendChildFactory,
    TurnToken,
)
from planner.conversation.contracts import ResolvedConversationStart
from planner.core.contracts import Principal
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    ChildCommand,
    CommandOutcome,
    CommandOutcomeReply,
    CommandOutcomeReport,
    CommandResult,
    ExecutorRefusal,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    JsonObject,
    LeaseRefused,
    LeaseReply,
    LeaseRequest,
    LiveEventBatch,
    LiveReadRequest,
    MailboxCommand,
    MailboxPollReply,
    ModelListRequest,
)
from planner.stalls.contracts import StallSource as StallSource

# The Mac's spool/commands.sqlite3, format COMMAND_JOURNAL_FORMAT_VERSION. Every command
# is recorded here before it runs, keyed by command_id, so a redelivered or restored
# command never runs twice. The poll cursor is MAX(command_sequence), 0 when empty.
# Lowering the cursor (the VPS's mailbox_high_sequence is below it, after a database
# restore) sets command_sequence=0 on every row above that high sequence: the rows stay
# for command_id deduplication, and MAX falls to the VPS's numbering, so the next poll
# fetches the commands the VPS numbers after it.
COMMAND_JOURNAL_SCHEMA: Final = """
CREATE TABLE commands (
  command_id TEXT PRIMARY KEY,
  command_sequence INTEGER NOT NULL,
  executor_incarnation TEXT NOT NULL,
  mailbox_command_json BLOB NOT NULL,
  phase TEXT NOT NULL CHECK(phase IN ('recorded','finished')),
  result_json BLOB,
  result_reported INTEGER NOT NULL DEFAULT 0 CHECK(result_reported IN (0,1)),
  outputs_published INTEGER NOT NULL DEFAULT 1 CHECK(outputs_published IN (0,1)),
  recorded_at_ms INTEGER NOT NULL);
CREATE INDEX unreported ON commands(command_sequence) WHERE phase='finished' AND result_reported=0;
"""

# present: an accepted lease within MAC_AWAY_AFTER_SECONDS, not going to sleep.
# asleep: the last lease said it was going to sleep. not_heard_from: leased before, but
# not within the limit. never: no accepted lease yet. refused: the latest contact was
# refused for its version, and no lease has been accepted since.
type PresenceClass = Literal["present", "asleep", "not_heard_from", "never", "refused"]


@dataclass(frozen=True, slots=True)
class PresenceRecord:
    """The mac_executor_presence row. Every time is the VPS clock."""

    executor_incarnation: str | None
    identity: ExecutorIdentity | None
    last_contact_at_ms: int | None
    last_lease_at_ms: int | None
    continuous_since_ms: int | None
    going_to_sleep_at_ms: int | None
    lease: LeaseRequest | None
    refusal: ExecutorRefusal | None


@dataclass(frozen=True, slots=True)
class PresenceJudgement:
    """since_ms: present since continuous_since_ms, asleep since the going-to-sleep lease,
    not heard from since the last lease, refused since the refusal; None for never."""

    presence: PresenceClass
    since_ms: int | None


@dataclass(frozen=True, slots=True)
class AcceptedLease:
    """What one accepted lease changed. replaced_incarnation is the previous current
    incarnation when this lease named a new one (None for the very first)."""

    previous: PresenceRecord | None
    current: PresenceRecord
    new_incarnation: bool
    replaced_incarnation: str | None


@dataclass(frozen=True, slots=True)
class ExecutorHealthRead:
    """GET /api/executor/health is {"mode": "remote", **this}. Computed from memory."""

    presence: PresenceClass
    since_ms: int | None
    identity: ExecutorIdentity | None
    last_contact_at_ms: int | None
    refusal: ExecutorRefusal | None
    queue_depth: int | None
    oldest_item_age_ms: int | None
    admission_stopped: bool


class MailboxCommandState(StrEnum):
    """executor_commands.state. Only waiting rows can be withdrawn; a delivered row whose
    process is replaced settles uncertain."""

    waiting = "waiting"
    delivered = "delivered"
    finished = "finished"
    withdrawn = "withdrawn"
    expired = "expired"


class CommandSettlementReason(StrEnum):
    """The result reason of a command the Mac did not finish, {"reason": <value>}."""

    # The command's incarnation is not (or no longer) current: withdrawn when still
    # waiting, uncertain when delivered, refused at put.
    executor_process_replaced = "executor_process_replaced"
    # The Mac journal found it recorded but unfinished at open: uncertain.
    executor_process_restarted = "executor_process_restarted"
    # Fetch-time expiry (refused). See executor/logic/command_expiry.py.
    steer_turn_not_running = "steer_turn_not_running"
    steer_delivery_limit_passed = "steer_delivery_limit_passed"
    answered_request_not_open = "answered_request_not_open"
    model_list_limit_passed = "model_list_limit_passed"


class MacRunConversations(Protocol):
    """What the executor's VPS half calls on the conversation core."""

    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
    ) -> None:
        """Run emit under the conversation lock, so a saved answer's mailbox row and the
        turn it answers cannot pass each other."""
        ...

    async def retire_replaced_remote_child(
        self, reference: ChildReference, *, replaced_by: str
    ) -> None:
        """After its drain: interrupt this conversation's turn of a replaced Mac process,
        then recover it like an interrupted VPS Ticket (a fresh child resumes the session)."""
        ...

    async def confirm_remote_child_termination(self, reference: ChildReference) -> None: ...

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None: ...


# VPS half (Part B). These name the pieces VpsExecutor composes.


class PresenceService(Protocol):
    """MacPresenceService: leases, the incarnation fence held in memory, and health."""

    async def accept_lease(self, request: LeaseRequest) -> LeaseReply | LeaseRefused:
        """Route 1. A new incarnation is made current in the same transaction that
        settles the old one's commands."""
        ...

    async def refuse_old_websocket(self) -> None:
        """Route 10: record ExecutorRefusal(MAC_NEEDS_UPDATE, peer_identity=None)."""
        ...

    def record_contact(self) -> None:
        """Contact without a lease (update routes). Kept in memory, flushed at most every
        CONTACT_FLUSH_INTERVAL_SECONDS."""
        ...

    def require_current(
        self, *, incarnation_header: str | None, protocol_header: str | None
    ) -> ExecutorIdentity:
        """The work-route header fence, from memory. Raises PlannerError
        executor_version_refused or executor_incarnation_not_current (409)."""
        ...

    def judgement(self) -> PresenceJudgement: ...

    def current_identity(self) -> ExecutorIdentity | None: ...

    def health(self) -> ExecutorHealthRead: ...

    def note_conversation_activity(self, conversation_id: str) -> None:
        """An applied item or an accepted live batch: activity for mac_turn_silent."""
        ...

    async def wait_until_drained(self, conversation_id: str) -> None:
        """Return after a lease that is not truncated and does not list conversation_id
        among its waiting conversations."""
        ...

    def stall_sources(self) -> tuple[StallSource, ...]: ...

    async def close(self) -> None: ...


class CommandMailbox(Protocol):
    """The executor_commands mailbox."""

    async def put_child_command(self, command: ChildCommand) -> MailboxCommand | CommandOutcome:
        """Commit the row (and, for start and write_prompt, the mac_runs update) in one
        BEGIN IMMEDIATE that also checks the current incarnation and the child's open
        assignment. On a mismatch nothing is inserted and the refused outcome
        (executor_process_replaced) is returned at once."""
        ...

    async def put_model_list_request(self, request: ModelListRequest) -> MailboxCommand: ...

    async def poll(
        self, identity: ExecutorIdentity, after: int, wait_seconds: float
    ) -> MailboxPollReply:
        """Route 2. Expire what has expired, mark eligible rows delivered, and answer them;
        hold for up to wait_seconds while none is eligible."""
        ...

    async def record_results(
        self, identity: ExecutorIdentity, report: CommandOutcomeReport
    ) -> CommandOutcomeReply:
        """Route 3. The first result written for a command wins; waiters wake once."""
        ...

    async def wait_result(
        self, command_id: str, timeout_seconds: float | None = None
    ) -> CommandResult | None: ...

    async def withdraw_waiting(self, command_id: str, reason: CommandSettlementReason) -> bool:
        """Withdraw a row only while it is still waiting. True if it was withdrawn."""
        ...

    async def finish_by_proof(self, command_id: str, outcome: CommandOutcome) -> None:
        """Settle a stop by its accepted child_stopped item."""
        ...

    def replace_incarnation_in_transaction(
        self, conn: sqlite3.Connection, *, replaced: str, current: str, now_ms: int
    ) -> tuple[str, ...]:
        """Withdraw the replaced incarnation's waiting rows and settle its delivered rows
        uncertain, inside the lease transaction. Returns the settled command ids, whose
        waiters are woken after the commit."""
        ...

    def high_sequence(self) -> int: ...

    def command_for_input(self, command_id: str) -> ChildCommand | None:
        """Route 6's scope: an unfinished, unwithdrawn child command, or None."""
        ...

    async def close(self) -> None: ...


class MacRunsApi(RetainedBackendChildFactory, Protocol):
    """MacRuns: run custody, children and their commands, through the mailbox."""

    conversations: MacRunConversations

    def assign_child(self, start: ResolvedConversationStart) -> ChildReference:
        """Needs a known compatible incarnation, not a fresh lease."""
        ...

    def authority(self, child: ChildReference) -> Principal: ...

    def owns_conversation(self, conversation_id: str) -> bool: ...

    def validate_stop(self, outcome: CommandOutcome) -> None: ...

    async def incarnation_replaced(self, replaced: str, current: str) -> None:
        """Start a drain task for each conversation with an open run of replaced."""
        ...

    async def resume_replacement_drains(self) -> None:
        """At startup: rebuild drain tasks from mac_runs compared with presence."""
        ...

    async def close(self) -> None: ...


# Mac half (Part C).


class MacHttpClient(Protocol):
    """Typed calls to the routes, one connector per loop, with per-route timeouts.

    Non-2xx answers raise ExecutorHttpError(status, code) (executor/mac_http.py), except
    a lease's 409, which returns its LeaseRefused body.
    """

    async def lease(self, request: LeaseRequest) -> LeaseReply | LeaseRefused: ...

    async def poll_commands(self, after: int, wait_seconds: float) -> MailboxPollReply: ...

    async def report_outcomes(self, report: CommandOutcomeReport) -> CommandOutcomeReply: ...

    async def post_items(self, conversation_id: str, batch: ItemBatch) -> ItemBatchReply: ...

    async def upload_file(self, upload: FileUploadRequest, source: Path) -> None: ...

    async def download_command_file(
        self, command_id: str, stored_file_id: str, target: Path
    ) -> str:
        """Stream route 6 into target and return the served X-Panels-File-Sha256."""
        ...

    async def read(self, request: LiveReadRequest) -> JsonObject: ...

    async def post_live(self, conversation_id: str, batch: LiveEventBatch) -> None: ...

    async def reset(self) -> None:
        """Replace every session, so the next request of each loop opens a new connection."""
        ...

    async def close(self) -> None: ...


class CommandJournalApi(Protocol):
    """spool/commands.sqlite3 (COMMAND_JOURNAL_SCHEMA), held under the outbox lock."""

    def poll_cursor(self) -> int:
        """MAX(command_sequence), 0 when empty."""
        ...

    def lower_poll_cursor(self, mailbox_high_sequence: int) -> bool:
        """When the cursor is above the VPS's high sequence, set command_sequence=0 on
        every row above it (kept for command_id deduplication), so the cursor falls to at
        most mailbox_high_sequence. True if any row was lowered."""
        ...

    def record_poll_reply(
        self, reply: MailboxPollReply, executor_incarnation: str
    ) -> tuple[MailboxCommand, ...]:
        """Record a whole reply in one transaction and return the commands to run: those
        whose command_id was not recorded before. A finished repeat is re-reported."""
        ...

    def result_of(self, command_id: str) -> CommandResult | None: ...

    def finish(self, command_id: str, result: CommandResult, *, outputs_owed: bool) -> None:
        """phase='finished', the result, and outputs_published=0 when outputs are owed."""
        ...

    def mark_outputs_published(self, command_id: str) -> None: ...

    def unpublished(self) -> tuple[tuple[MailboxCommand, CommandResult], ...]: ...

    def unreported(self, limit: int) -> tuple[CommandResult, ...]: ...

    def mark_reported(self, command_ids: Iterable[str]) -> None: ...

    def prune(self) -> None:
        """Delete finished, reported, published rows except the one with the highest
        sequence, which keeps the cursor."""
        ...

    def close(self) -> None: ...


class MacFilesApi(Protocol):
    """Uploads and input downloads, at most MAX_FILE_TRANSFERS at once."""

    async def upload(self, upload: FileUploadRequest, source: Path) -> None: ...

    async def download_inputs(self, command: ChildCommand) -> None:
        """Install every message file a prompt or steer carries, verified by digest."""
        ...
