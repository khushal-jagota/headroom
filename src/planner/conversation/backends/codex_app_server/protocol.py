"""The Codex wire that Panels actually uses.

These contracts belong to Panels, not a particular Codex release. Unknown fields are
ignored. Only consumed item kinds are decoded, so new upstream items cannot invalidate
an otherwise usable turn. Identities, outcomes and permission decisions stay strict.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints

Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class ClientInfo(WireModel):
    name: str
    title: str | None = None
    version: str


class InitializeCapabilities(WireModel):
    experimentalApi: bool


class InitializeParams(WireModel):
    clientInfo: ClientInfo
    capabilities: InitializeCapabilities | None = None


class TextUserInput(WireModel):
    type: Literal["text"]
    text: str


class LocalImageUserInput(WireModel):
    type: Literal["localImage"]
    path: str


class SkillUserInput(WireModel):
    type: Literal["skill"]
    name: str
    path: str


class MentionUserInput(WireModel):
    type: Literal["mention"]
    name: str
    path: str


type UserInput = TextUserInput | LocalImageUserInput | SkillUserInput | MentionUserInput


class DangerFullAccessSandboxPolicy(WireModel):
    type: Literal["dangerFullAccess"]


class WorkspaceWriteSandboxPolicy(WireModel):
    """The permission exercise's native mapping, separate from production full access."""

    type: Literal["workspaceWrite"]


class ThreadStartParams(WireModel):
    cwd: str
    model: str | None = None
    approvalPolicy: Literal["never", "untrusted"]
    approvalsReviewer: Literal["user"]
    sandbox: Literal["danger-full-access", "workspace-write"]
    developerInstructions: str | None = None


class ThreadResumeParams(ThreadStartParams):
    threadId: Identifier


class TurnStartParams(WireModel):
    threadId: Identifier
    input: list[UserInput]
    model: str | None = None
    effort: str | None = None
    approvalPolicy: Literal["never", "untrusted"]
    approvalsReviewer: Literal["user"]
    sandboxPolicy: DangerFullAccessSandboxPolicy | WorkspaceWriteSandboxPolicy


class TurnSteerParams(WireModel):
    threadId: Identifier
    expectedTurnId: Identifier
    input: list[UserInput]


class TurnSteerResponse(WireModel):
    turnId: Identifier


class TurnInterruptParams(WireModel):
    threadId: Identifier
    turnId: Identifier


class Thread(WireModel):
    id: Identifier
    ephemeral: bool


class ThreadStartResponse(WireModel):
    thread: Thread


class ThreadResumeResponse(ThreadStartResponse):
    pass


class TurnError(WireModel):
    message: str
    codexErrorInfo: str | dict[str, Any] | None = None
    additionalDetails: str | None = None


class Turn(WireModel):
    id: Identifier
    status: Literal["inProgress", "completed", "failed", "interrupted"]
    error: TurnError | None = None


class TurnStartResponse(WireModel):
    turn: Turn


class ThreadTurnNotification(WireModel):
    threadId: Identifier
    turn: Turn


class TurnStartedNotification(ThreadTurnNotification):
    pass


class TurnCompletedNotification(ThreadTurnNotification):
    pass


class TurnNotification(WireModel):
    threadId: Identifier
    turnId: Identifier


class MessageDeltaNotification(TurnNotification):
    itemId: Identifier
    delta: str


class AgentMessageDeltaNotification(MessageDeltaNotification):
    pass


class CommandExecutionOutputDeltaNotification(MessageDeltaNotification):
    pass


class McpToolCallProgressNotification(TurnNotification):
    itemId: Identifier
    message: str


class ReasoningTextDeltaNotification(TurnNotification):
    pass


class ReasoningSummaryTextDeltaNotification(TurnNotification):
    pass


class AgentMessageThreadItem(WireModel):
    type: Literal["agentMessage"]
    id: Identifier
    text: str


class ReasoningThreadItem(WireModel):
    type: Literal["reasoning"]


class ContextCompactionThreadItem(WireModel):
    type: Literal["contextCompaction"]


class CommandExecutionThreadItem(WireModel):
    type: Literal["commandExecution"]
    id: Identifier
    command: str
    cwd: str
    status: Literal["inProgress", "completed", "failed", "declined"]
    aggregatedOutput: str | None = None


class FileChangeKind(WireModel):
    type: str


class FileChange(WireModel):
    path: str
    kind: FileChangeKind


class FileChangeThreadItem(WireModel):
    type: Literal["fileChange"]
    id: Identifier
    changes: list[FileChange]
    status: Literal["inProgress", "completed", "failed", "declined"]


class McpToolCallError(WireModel):
    message: str


class McpToolCallThreadItem(WireModel):
    type: Literal["mcpToolCall"]
    id: Identifier
    server: str
    tool: str
    status: Literal["inProgress", "completed", "failed"]
    error: McpToolCallError | None = None


class UnconsumedThreadItem(WireModel):
    """No identity is needed for an item Panels drops without reading it."""

    type: str


_ITEM_MODELS: dict[str, type[WireModel]] = {
    "agentMessage": AgentMessageThreadItem,
    "reasoning": ReasoningThreadItem,
    "contextCompaction": ContextCompactionThreadItem,
    "commandExecution": CommandExecutionThreadItem,
    "fileChange": FileChangeThreadItem,
    "mcpToolCall": McpToolCallThreadItem,
}


def _decode_item(value: Any) -> WireModel:
    if isinstance(value, WireModel):
        return value
    if not isinstance(value, dict):
        raise ValueError("Codex item must be an object")
    tag = value.get("type")
    if not isinstance(tag, str):
        raise ValueError("Codex item type must be a string")
    return _ITEM_MODELS.get(tag, UnconsumedThreadItem).model_validate(value)


type ThreadItem = Annotated[
    AgentMessageThreadItem
    | ReasoningThreadItem
    | ContextCompactionThreadItem
    | CommandExecutionThreadItem
    | FileChangeThreadItem
    | McpToolCallThreadItem
    | UnconsumedThreadItem,
    BeforeValidator(_decode_item),
]


class ItemNotification(TurnNotification):
    item: ThreadItem


class ItemStartedNotification(ItemNotification):
    pass


class ItemCompletedNotification(ItemNotification):
    pass


class TurnPlanStep(WireModel):
    step: str
    status: Literal["pending", "inProgress", "completed"]


class TurnPlanUpdatedNotification(TurnNotification):
    plan: list[TurnPlanStep]


class TokenUsage(WireModel):
    inputTokens: int
    outputTokens: int
    cachedInputTokens: int


class ThreadTokenUsage(WireModel):
    total: TokenUsage


class ThreadTokenUsageUpdatedNotification(TurnNotification):
    tokenUsage: ThreadTokenUsage


class ErrorNotification(TurnNotification):
    error: TurnError
    willRetry: bool


class SkillsListParams(WireModel):
    cwds: list[str]


class DisplayInterface(WireModel):
    displayName: str | None = None
    shortDescription: str | None = None


class SkillMetadata(WireModel):
    name: str
    path: str
    enabled: bool
    description: str
    shortDescription: str | None = None
    interface: DisplayInterface | None = None


class SkillsListEntry(WireModel):
    skills: list[SkillMetadata]


class SkillsListResponse(WireModel):
    data: list[SkillsListEntry]


class SkillsChangedNotification(WireModel):
    pass


class AppsInstalledParams(WireModel):
    threadId: Identifier
    forceRefresh: bool


class InstalledApp(WireModel):
    id: Identifier
    enabled: bool
    callable: bool
    runtimeName: str | None = None


class AppsInstalledResponse(WireModel):
    apps: list[InstalledApp]


class AppsReadParams(WireModel):
    appIds: list[str]
    threadId: Identifier
    includeTools: bool


class ConnectorMetadata(WireModel):
    id: Identifier
    name: str
    description: str | None = None


class AppsReadResponse(WireModel):
    apps: list[ConnectorMetadata]


class AppListUpdatedNotification(WireModel):
    pass


class PluginInstalledParams(WireModel):
    cwds: list[str]


class PluginSummary(WireModel):
    name: str
    installed: bool
    enabled: bool
    availability: str | None = None
    disabledReason: str | None = None
    interface: DisplayInterface | None = None


class PluginMarketplaceEntry(WireModel):
    name: str
    plugins: list[PluginSummary]


class PluginInstalledResponse(WireModel):
    marketplaces: list[PluginMarketplaceEntry]


class ThreadCompactStartParams(WireModel):
    threadId: Identifier


class ThreadCompactStartResponse(WireModel):
    pass


class CustomReviewTarget(WireModel):
    type: Literal["custom"]
    instructions: str


class UncommittedChangesReviewTarget(WireModel):
    type: Literal["uncommittedChanges"]


class ReviewStartParams(WireModel):
    threadId: Identifier
    delivery: Literal["inline"]
    target: CustomReviewTarget | UncommittedChangesReviewTarget


class ReviewStartResponse(TurnStartResponse):
    pass


class ThreadGoalGetParams(WireModel):
    threadId: Identifier


class ThreadGoal(WireModel):
    objective: str
    status: str
    timeUsedSeconds: int
    tokenBudget: int | None = None
    tokensUsed: int


class ThreadGoalGetResponse(WireModel):
    goal: ThreadGoal | None = None


class ThreadGoalSetParams(WireModel):
    threadId: Identifier
    objective: str | None = None
    status: Literal["active"]


class ThreadGoalSetResponse(WireModel):
    goal: ThreadGoal


class ThreadGoalClearParams(WireModel):
    threadId: Identifier


class ThreadGoalClearResponse(WireModel):
    cleared: bool


class ReasoningEffortOption(WireModel):
    reasoningEffort: str


class CatalogModel(WireModel):
    model: str
    displayName: str
    hidden: bool
    supportedReasoningEfforts: list[ReasoningEffortOption]
    defaultReasoningEffort: str
    isDefault: bool


class ModelListResponse(WireModel):
    data: list[CatalogModel]
    nextCursor: str | None = None


class CommandExecutionRequestApprovalParams(WireModel):
    threadId: Identifier
    turnId: Identifier
    itemId: Identifier
    command: str | None = None
    cwd: str | None = None
    reason: str | None = None


class FileChangeRequestApprovalParams(WireModel):
    threadId: Identifier
    turnId: Identifier
    reason: str | None = None
    grantRoot: str | None = None


class CommandExecutionRequestApprovalResponse(WireModel):
    decision: Literal["accept", "acceptForSession", "decline", "cancel"]


class FileChangeRequestApprovalResponse(CommandExecutionRequestApprovalResponse):
    pass


class ToolRequestUserInputOption(WireModel):
    label: str
    description: str


class ToolRequestUserInputQuestion(WireModel):
    id: Identifier
    header: str
    question: str
    isSecret: bool = False
    isOther: bool = False
    options: list[ToolRequestUserInputOption] | None = None


class ToolRequestUserInputParams(WireModel):
    threadId: Identifier
    turnId: Identifier
    itemId: Identifier
    questions: list[ToolRequestUserInputQuestion]


class ToolRequestUserInputAnswer(WireModel):
    answers: list[str]


class ToolRequestUserInputResponse(WireModel):
    answers: dict[str, ToolRequestUserInputAnswer]


class DeviceCodeLoginResponse(WireModel):
    type: Literal["chatgptDeviceCode"]
    loginId: Identifier
    userCode: Identifier
    verificationUrl: Identifier


class AccountLoginCompletedNotification(WireModel):
    loginId: Identifier | None = None
    success: bool
    error: str | None = None
