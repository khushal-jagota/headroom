"""Canonical selected-view protocol. Browser types are generated from this module.

The durable event record remains authoritative. A projection references source bodies;
these response types never authorize another copy of the complete conversation record.
All sequence values are scoped to the named conversation, not global database row IDs.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from planner.conversation.contracts import PromptDeliveryMode, PromptDeliveryRefusalReason
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationTurnEnding,
    PermissionAskOption,
    PlanEntry,
    UserInputAnswer,
    UserInputQuestion,
)
from planner.core.contracts import Principal

VIEW_PROTOCOL_VERSION = 1
VIEW_PROJECTION_VERSION = 1

type ConversationViewLens = Literal["focus", "full"]
type ViewRowKind = Literal[
    "prompt",
    "prompt_refused",
    "prompt_uncertain",
    "prompt_discarded",
    "agent_message",
    "explicit_reply_missing",
    "tool_call",
    "permission_ask",
    "user_input",
    "model_changed",
    "token_usage",
    "context_compacted",
    "turn_ended",
    "turn_stopped",
]
type AskDeadReason = Literal["turn_ended", "no_ending_recorded"]


class ViewTextPiece(TypedDict):
    piece: Literal["text"]
    text: str


class ViewImagePiece(TypedDict):
    piece: Literal["image"]
    stored_file_id: str
    media_type: str
    file_name: str | None


class ViewFilePiece(TypedDict):
    piece: Literal["file"]
    stored_file_id: str
    media_type: str
    file_name: str
    byte_count: int


type ViewMessagePiece = ViewTextPiece | ViewImagePiece | ViewFilePiece


class PromptRowBody(TypedDict):
    kind: Literal["prompt"]
    content: list[ViewMessagePiece]
    sender_label: str
    mode: PromptDeliveryMode
    sent_at_unix_milliseconds: int


class RefusedPromptRowBody(TypedDict):
    kind: Literal["prompt_refused"]
    content: list[ViewMessagePiece]
    sender_label: str
    reason: PromptDeliveryRefusalReason
    refusal_detail: str | None


class UndeliveredPromptRowBody(TypedDict):
    kind: Literal["prompt_uncertain", "prompt_discarded"]
    content: list[ViewMessagePiece]
    sender_label: str


class AgentMessageRowBody(TypedDict):
    kind: Literal["agent_message"]
    content: list[ViewMessagePiece]
    to_owner: bool


class MissingReplyRowBody(TypedDict):
    kind: Literal["explicit_reply_missing"]
    prompt_sender: Principal


class ToolCallRowBody(TypedDict):
    kind: Literal["tool_call"]
    tool_call_id: str
    title: str
    tool_kind: str
    detail: str | None
    started_detail: str | None
    # Preserve the current short detail and the existing expansion to full output.
    capped_detail_sequence: int | None
    status: Literal["running", "completed", "failed"]


class PermissionRowBody(TypedDict):
    kind: Literal["permission_ask"]
    ask_id: str
    title: str
    detail: str | None
    options: list[PermissionAskOption]
    state: Literal["live", "answered", "dead"]
    dead_reason: AskDeadReason | None
    answered_option_label: str | None


class UserInputRowBody(TypedDict):
    kind: Literal["user_input"]
    request_id: str
    questions: list[UserInputQuestion]
    state: Literal["live", "answered", "failed", "dead"]
    dead_reason: AskDeadReason | None
    answers: list[UserInputAnswer] | None
    failure_detail: str | None


class ModelRowBody(TypedDict):
    kind: Literal["model_changed"]
    model: str | None
    reasoning_effort: str | None


class UsageRowBody(TypedDict):
    kind: Literal["token_usage"]
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cost_usd: float | None


class TurnEndingRowBody(TypedDict):
    kind: Literal["turn_ended"]
    ending: ConversationTurnEnding
    error_summary: str | None
    automatic_compaction_result: AutomaticCompactionResult | None


class MarkerRowBody(TypedDict):
    kind: Literal["context_compacted", "turn_stopped"]


type ViewRowBody = (
    PromptRowBody
    | RefusedPromptRowBody
    | UndeliveredPromptRowBody
    | AgentMessageRowBody
    | MissingReplyRowBody
    | ToolCallRowBody
    | PermissionRowBody
    | UserInputRowBody
    | ModelRowBody
    | UsageRowBody
    | TurnEndingRowBody
    | MarkerRowBody
)


class ViewBodyReference(TypedDict):
    # Opaque server locator binds conversation, row identity and body version.
    # It is not an arbitrary URL or a path supplied by a caller.
    body_id: str
    version: str
    byte_count: int
    media_type: Literal["application/json", "text/markdown", "text/plain"]


class InlineRowBody(TypedDict):
    kind: Literal["inline"]
    value: ViewRowBody


class DeferredRowBody(TypedDict):
    kind: Literal["deferred"]
    reference: ViewBodyReference


type DeliveredRowBody = InlineRowBody | DeferredRowBody


class ViewOrder(TypedDict):
    # Heads occupy the slot after their prompt. Work groups occupy their first row's
    # slot. Lexicographic order remains stable when a row is updated or removed.
    sequence: int
    slot: int


class ConversationViewRow(TypedDict):
    kind: Literal["row"]
    key: str
    row_kind: ViewRowKind
    order: ViewOrder
    source_sequence: int
    sender_message_id: str | None
    changed_through_sequence: int
    created_at: int
    turn_key: str | None
    work_group_key: str | None
    # A turn aggregate determines whether earlier messages fold. No mass row rewrite.
    fold_rule: Literal["never", "earlier_message", "work"]
    body: DeliveredRowBody


class ConversationViewTurn(TypedDict):
    kind: Literal["turn"]
    key: str
    order: ViewOrder
    changed_through_sequence: int
    settled: bool
    stopped: bool
    started_at_unix_milliseconds: int | None
    ending: ConversationTurnEnding | None
    duration_seconds: int | None
    tool_call_count: int
    folded_message_count: int
    latest_message_key: str | None
    is_latest: bool


class ConversationViewWorkGroup(TypedDict):
    kind: Literal["work_group"]
    key: str
    order: ViewOrder
    changed_through_sequence: int
    turn_key: str | None
    entry_count: int
    latest_entry_key: str
    settled: bool
    # Children have their own bounded pages. Never inline an entire work run.


type ConversationViewItem = ConversationViewRow | ConversationViewTurn | ConversationViewWorkGroup


class ViewHistoryScope(TypedDict):
    # Timeline excludes collapsed children; turn/work scopes expose their existing
    # expansion surfaces. Their bodies all share one reader cache budget.
    kind: Literal["timeline", "turn", "work_group"]
    key: str | None


class ViewPageCursor(TypedDict):
    projection_version: int
    lens: ConversationViewLens
    scope: ViewHistoryScope
    order: ViewOrder


class ViewCoverage(TypedDict):
    # Independent from page order and viewport residency. Hidden Focus events count.
    # Only snapshot/tail delivery, under the existing read policy, can acknowledge it.
    through_sequence: int


class ConversationViewPage(TypedDict):
    conversation_id: str
    protocol_version: int
    projection_version: int
    lens: ConversationViewLens
    scope: ViewHistoryScope
    items: list[ConversationViewItem]
    older: ViewPageCursor | None
    newer: ViewPageCursor | None
    body_bytes: int
    # A historical page is not a read receipt and carries no coverage acknowledgement.


class ViewRequestIdentity(TypedDict):
    kind: Literal["permission", "user_input"]
    request_id: str
    row_key: str
    source_sequence: int


class ViewLiveOutput(TypedDict):
    runtime_epoch: str
    turn_key: str
    version: int
    byte_count: int
    reference: ViewBodyReference


class ViewRestText(TypedDict):
    kind: Literal["prompt", "agent_message", "streaming_agent_message"]
    sender_label: str | None
    # First line only, under the existing 120-character rest-line rule.
    excerpt: str


class ViewRestDelivery(TypedDict):
    kind: Literal["prompt_refused", "prompt_uncertain", "prompt_discarded"]
    sender_label: str
    refusal_reason: PromptDeliveryRefusalReason | None
    refusal_detail_excerpt: str | None


class ViewRestTool(TypedDict):
    kind: Literal["tool_call"]
    tool_call_id: str
    title: str
    tool_kind: str
    # Canonical presentation inputs live behind a reference when above the status
    # budget. Existing tool presentation owns wording, not a new server formatter.
    body: DeliveredRowBody


type ViewRestEvent = (
    ViewRestText
    | ViewRestDelivery
    | ViewRestTool
    | MissingReplyRowBody
    | TurnEndingRowBody
    | MarkerRowBody
)


class ViewPlanProgress(TypedDict):
    total_count: int
    completed_count: int
    current_position: int | None
    current_text_excerpt: str | None
    has_unfinished_entry: bool


class ViewRestFacts(TypedDict):
    latest_happened: ViewRestEvent | None
    latest_turn: ConversationViewTurn | None
    latest_tool: ViewRestTool | None
    permission_title_excerpt: str | None
    latest_owner_message_sequence: int
    last_boundary_failed: bool
    plan_progress: ViewPlanProgress


class ConversationViewStatus(TypedDict):
    diagnostics_expired: bool
    runtime_epoch: str
    revision: int
    activity_at_unix_milliseconds: int | None
    rest: ViewRestFacts
    authority: Literal["current", "unknown"]
    turn_key: str | None
    turn_running: bool | None
    stopped_without_ending: bool
    pending_permission: ViewRequestIdentity | None
    pending_user_input: ViewRequestIdentity | None
    # Plan bodies can also be oversized. Their versioned source is fetched on demand.
    plan: ViewBodyReference | None
    live_output: ViewLiveOutput | None
    # Latest activity is compact status. Other visible active tools fetch versioned
    # progress bodies by identity, under the same bounded reader body budget.
    live_tool_key: str | None
    live_tool_progress: ViewBodyReference | None


class ConversationViewSnapshot(TypedDict):
    page: ConversationViewPage
    coverage: ViewCoverage
    status: ConversationViewStatus


class ViewRetainedRange(TypedDict):
    scope: ViewHistoryScope
    first: ViewOrder
    last: ViewOrder


class ViewScopeChanges(TypedDict):
    scope: ViewHistoryScope
    item_keys: list[str]
    removed_keys: list[str]


class ViewTailChanges(TypedDict):
    kind: Literal["changes"]
    conversation_id: str
    protocol_version: int
    projection_version: int
    lens: ConversationViewLens
    from_sequence: int
    coverage: ViewCoverage
    # Only changes intersecting requested ranges and the recent timeline are hydrated.
    upserts: list[ConversationViewItem]
    scope_changes: list[ViewScopeChanges]
    removed_keys: list[str]
    invalidated_scopes: list[ViewHistoryScope]
    status: ConversationViewStatus


class ViewTailStatus(TypedDict):
    kind: Literal["status"]
    conversation_id: str
    lens: ConversationViewLens
    coverage: ViewCoverage
    status: ConversationViewStatus


class ViewTailReset(TypedDict):
    kind: Literal["reset"]
    reason: Literal["gap", "overflow", "projection_changed", "runtime_changed"]


type ConversationViewTail = ViewTailChanges | ViewTailStatus | ViewTailReset


class ViewBodyChunk(TypedDict):
    reference: ViewBodyReference
    offset: int
    # UTF-8 aligned offsets. Transport chunks are never Markdown parse boundaries.
    text: str
    next_offset: int | None


class ViewPlanBody(TypedDict):
    entries: list[PlanEntry]


class ViewMessageReceipt(TypedDict):
    sender_message_id: str
    source_sequence: int
    fate: Literal["prompt", "prompt_refused", "prompt_uncertain", "prompt_discarded"]


class ViewMessageReceipts(TypedDict):
    conversation_id: str
    coverage: ViewCoverage
    receipts: list[ViewMessageReceipt]
