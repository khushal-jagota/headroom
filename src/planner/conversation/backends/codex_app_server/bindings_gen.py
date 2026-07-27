"""Codex app-server protocol bindings. Generated — do not edit.

Regenerate with:
    python -m planner.conversation.backends.codex_app_server.generate_bindings

The pin these models were generated under:

    codex binary version   codex-cli 0.145.0
    upstream openai/codex  rust-v0.145.0 = 25af12f7e61572b0bc18ddb1008be543b91519b0
    schema obtained by     codex app-server generate-json-schema --out <dir>
    from the dump's        codex_app_server_protocol.schemas.json
    dump digest (sha256)   5469280cfbdaa12f6d28e2206f942da808f8b699ce6c43b13f2439d843432f38
    pruned and vendored    schema/codex_app_server_protocol.subset.schema.json
    subset digest (sha256) 4baa7cc1e44c69b08bed0ff7712e703e7e6154ffeb1a9b7e5dfc2ced099058aa
    definitions generated  104

The digests are taken over the JSON's meaning — keys sorted — so they change
when the protocol changes and not when the dump is printed differently.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class Model(RootModel[Any]):
    root: Any


class ClientInfo(BaseModel):
    name: str
    title: str | None = None
    version: str


class AcceptWithExecpolicyAmendment(BaseModel):
    execpolicy_amendment: list[str]


class AcceptWithExecpolicyAmendmentCommandExecutionApprovalDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    acceptWithExecpolicyAmendment: AcceptWithExecpolicyAmendment


class FileChangeRequestApprovalParams(BaseModel):
    grantRoot: str | None = None
    itemId: str
    reason: str | None = None
    startedAtMs: int
    threadId: str
    turnId: str


class FileChangeRequestApprovalResponse(BaseModel):
    decision: (
        Literal["accept"] | Literal["acceptForSession"] | Literal["decline"] | Literal["cancel"]
    )


class InitializeCapabilities(BaseModel):
    experimentalApi: bool | None = False
    mcpServerOpenaiFormElicitation: bool | None = None
    optOutNotificationMethods: list[str] | None = None
    requestAttestation: bool | None = False


class InitializeParams(BaseModel):
    capabilities: InitializeCapabilities | None = None
    clientInfo: ClientInfo


class ApiKeyAccount(BaseModel):
    type: Annotated[Literal["apiKey"], Field(title="ApiKeyAccountType")]


class AmazonBedrockAccount(BaseModel):
    type: Annotated[Literal["amazonBedrock"], Field(title="AmazonBedrockAccountType")]
    usesCodexManagedCredentials: bool | None = False


class AgentMessageDeltaNotification(BaseModel):
    delta: str
    itemId: str
    threadId: str
    turnId: str


class Granular(BaseModel):
    mcp_elicitations: bool
    request_permissions: bool | None = False
    rules: bool
    sandbox_approval: bool
    skill_approval: bool | None = False


class GranularAskForApproval(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    granular: Granular


class ByteRange(BaseModel):
    end: Annotated[int, Field(ge=0)]
    start: Annotated[int, Field(ge=0)]


class HttpConnectionFailed(BaseModel):
    httpStatusCode: Annotated[int | None, Field(ge=0)] = None


class HttpConnectionFailedCodexErrorInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    httpConnectionFailed: HttpConnectionFailed


class ResponseStreamConnectionFailed(BaseModel):
    httpStatusCode: Annotated[int | None, Field(ge=0)] = None


class ResponseStreamConnectionFailedCodexErrorInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    responseStreamConnectionFailed: ResponseStreamConnectionFailed


class ResponseStreamDisconnected(BaseModel):
    httpStatusCode: Annotated[int | None, Field(ge=0)] = None


class ResponseStreamDisconnectedCodexErrorInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    responseStreamDisconnected: ResponseStreamDisconnected


class ResponseTooManyFailedAttempts(BaseModel):
    httpStatusCode: Annotated[int | None, Field(ge=0)] = None


class ResponseTooManyFailedAttemptsCodexErrorInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    responseTooManyFailedAttempts: ResponseTooManyFailedAttempts


class ReadCommandAction(BaseModel):
    command: str
    name: str
    path: str
    type: Annotated[Literal["read"], Field(title="ReadCommandActionType")]


class ListFilesCommandAction(BaseModel):
    command: str
    path: str | None = None
    type: Annotated[Literal["listFiles"], Field(title="ListFilesCommandActionType")]


class SearchCommandAction(BaseModel):
    command: str
    path: str | None = None
    query: str | None = None
    type: Annotated[Literal["search"], Field(title="SearchCommandActionType")]


class UnknownCommandAction(BaseModel):
    command: str
    type: Annotated[Literal["unknown"], Field(title="UnknownCommandActionType")]


class CommandExecutionOutputDeltaNotification(BaseModel):
    delta: str
    itemId: str
    threadId: str
    turnId: str


class InputTextDynamicToolCallOutputContentItem(BaseModel):
    text: str
    type: Annotated[
        Literal["inputText"], Field(title="InputTextDynamicToolCallOutputContentItemType")
    ]


class InputImageDynamicToolCallOutputContentItem(BaseModel):
    imageUrl: str
    type: Annotated[
        Literal["inputImage"], Field(title="InputImageDynamicToolCallOutputContentItemType")
    ]


class InputAudioDynamicToolCallOutputContentItem(BaseModel):
    audioUrl: str
    type: Annotated[
        Literal["inputAudio"], Field(title="InputAudioDynamicToolCallOutputContentItemType")
    ]


class GetAccountParams(BaseModel):
    refreshToken: bool | None = None


class GitInfo(BaseModel):
    branch: str | None = None
    originUrl: str | None = None
    sha: str | None = None


class HookPromptFragment(BaseModel):
    hookRunId: str
    text: str


class McpToolCallAppContext(BaseModel):
    actionName: str | None = None
    appName: str | None = None
    connectorId: str
    linkId: str | None = None
    resourceUri: str | None = None


class McpToolCallError(BaseModel):
    message: str


class McpToolCallProgressNotification(BaseModel):
    itemId: str
    message: str
    threadId: str
    turnId: str


class McpToolCallResult(BaseModel):
    field_meta: Annotated[Any | None, Field(alias="_meta")] = None
    content: list[Any]
    structuredContent: Any | None = None


class MemoryCitationEntry(BaseModel):
    lineEnd: Annotated[int, Field(ge=0)]
    lineStart: Annotated[int, Field(ge=0)]
    note: str
    path: str


class ModelAvailabilityNux(BaseModel):
    message: str


class ModelListParams(BaseModel):
    cursor: str | None = None
    includeHidden: bool | None = None
    limit: Annotated[int | None, Field(ge=0)] = None


class ModelServiceTier(BaseModel):
    id: str
    name: str


class ModelUpgradeInfo(BaseModel):
    migrationMarkdown: str | None = None
    model: str
    modelLink: str | None = None
    upgradeCopy: str | None = None


class AddPatchChangeKind(BaseModel):
    type: Annotated[Literal["add"], Field(title="AddPatchChangeKindType")]


class DeletePatchChangeKind(BaseModel):
    type: Annotated[Literal["delete"], Field(title="DeletePatchChangeKindType")]


class UpdatePatchChangeKind(BaseModel):
    move_path: str | None = None
    type: Annotated[Literal["update"], Field(title="UpdatePatchChangeKindType")]


class ReasoningEffort(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class ReasoningEffortOption(BaseModel):
    reasoningEffort: Annotated[str, Field(min_length=1)]


class ReasoningSummaryTextDeltaNotification(BaseModel):
    delta: str
    itemId: str
    summaryIndex: int
    threadId: str
    turnId: str


class ReasoningTextDeltaNotification(BaseModel):
    contentIndex: int
    delta: str
    itemId: str
    threadId: str
    turnId: str


class DangerFullAccessSandboxPolicy(BaseModel):
    type: Annotated[Literal["dangerFullAccess"], Field(title="DangerFullAccessSandboxPolicyType")]


class ReadOnlySandboxPolicy(BaseModel):
    networkAccess: bool | None = False
    type: Annotated[Literal["readOnly"], Field(title="ReadOnlySandboxPolicyType")]


class ExternalSandboxSandboxPolicy(BaseModel):
    networkAccess: Literal["restricted", "enabled"] | None = "restricted"
    type: Annotated[Literal["externalSandbox"], Field(title="ExternalSandboxSandboxPolicyType")]


class WorkspaceWriteSandboxPolicy(BaseModel):
    excludeSlashTmp: bool | None = False
    excludeTmpdirEnvVar: bool | None = False
    networkAccess: bool | None = False
    type: Annotated[Literal["workspaceWrite"], Field(title="WorkspaceWriteSandboxPolicyType")]
    writableRoots: list[str] | None = []


class CustomSessionSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    custom: str


class OtherSubAgentSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    other: str


class TextElement(BaseModel):
    byteRange: ByteRange
    placeholder: str | None = None


class HookPromptThreadItem(BaseModel):
    fragments: list[HookPromptFragment]
    id: str
    type: Annotated[Literal["hookPrompt"], Field(title="HookPromptThreadItemType")]


class PlanThreadItem(BaseModel):
    id: str
    text: str
    type: Annotated[Literal["plan"], Field(title="PlanThreadItemType")]


class ReasoningThreadItem(BaseModel):
    content: list[str] | None = []
    id: str
    summary: list[str] | None = []
    type: Annotated[Literal["reasoning"], Field(title="ReasoningThreadItemType")]


class CommandExecutionThreadItem(BaseModel):
    aggregatedOutput: str | None = None
    command: str
    commandActions: list[
        ReadCommandAction | ListFilesCommandAction | SearchCommandAction | UnknownCommandAction
    ]
    cwd: str
    durationMs: int | None = None
    exitCode: int | None = None
    id: str
    processId: str | None = None
    source: Literal["agent", "userShell", "unifiedExecStartup", "unifiedExecInteraction"] | None = (
        "agent"
    )
    status: Literal["inProgress", "completed", "failed", "declined"]
    type: Annotated[Literal["commandExecution"], Field(title="CommandExecutionThreadItemType")]


class McpToolCallThreadItem(BaseModel):
    appContext: McpToolCallAppContext | None = None
    arguments: Any
    durationMs: int | None = None
    error: McpToolCallError | None = None
    id: str
    mcpAppResourceUri: str | None = None
    pluginId: str | None = None
    result: McpToolCallResult | None = None
    server: str
    status: Literal["inProgress", "completed", "failed"]
    tool: str
    type: Annotated[Literal["mcpToolCall"], Field(title="McpToolCallThreadItemType")]


class DynamicToolCallThreadItem(BaseModel):
    arguments: Any
    contentItems: (
        list[
            InputTextDynamicToolCallOutputContentItem
            | InputImageDynamicToolCallOutputContentItem
            | InputAudioDynamicToolCallOutputContentItem
        ]
        | None
    ) = None
    durationMs: int | None = None
    id: str
    namespace: str | None = None
    status: Literal["inProgress", "completed", "failed"]
    success: bool | None = None
    tool: str
    type: Annotated[Literal["dynamicToolCall"], Field(title="DynamicToolCallThreadItemType")]


class SubAgentActivityThreadItem(BaseModel):
    agentPath: str
    agentThreadId: str
    id: str
    kind: Literal["started", "interacted", "interrupted"]
    type: Annotated[Literal["subAgentActivity"], Field(title="SubAgentActivityThreadItemType")]


class ImageViewThreadItem(BaseModel):
    id: str
    path: str
    type: Annotated[Literal["imageView"], Field(title="ImageViewThreadItemType")]


class SleepThreadItem(BaseModel):
    durationMs: Annotated[int, Field(ge=0)]
    id: str
    type: Annotated[Literal["sleep"], Field(title="SleepThreadItemType")]


class ImageGenerationThreadItem(BaseModel):
    id: str
    result: str
    revisedPrompt: str | None = None
    savedPath: str | None = None
    status: str
    type: Annotated[Literal["imageGeneration"], Field(title="ImageGenerationThreadItemType")]


class EnteredReviewModeThreadItem(BaseModel):
    id: str
    review: str
    type: Annotated[Literal["enteredReviewMode"], Field(title="EnteredReviewModeThreadItemType")]


class ExitedReviewModeThreadItem(BaseModel):
    id: str
    review: str
    type: Annotated[Literal["exitedReviewMode"], Field(title="ExitedReviewModeThreadItemType")]


class ContextCompactionThreadItem(BaseModel):
    id: str
    type: Annotated[Literal["contextCompaction"], Field(title="ContextCompactionThreadItemType")]


class ThreadResumeParams(BaseModel):
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval | None = (
        None
    )
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"] | None = None
    baseInstructions: str | None = None
    config: dict[str, Any] | None = None
    cwd: str | None = None
    developerInstructions: str | None = None
    serviceTier: str | None = None
    threadId: str
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    model: str | None = None
    modelProvider: str | None = None
    personality: Literal["none", "friendly", "pragmatic"] | None = None


class NotLoadedThreadStatus(BaseModel):
    type: Annotated[Literal["notLoaded"], Field(title="NotLoadedThreadStatusType")]


class IdleThreadStatus(BaseModel):
    type: Annotated[Literal["idle"], Field(title="IdleThreadStatusType")]


class SystemErrorThreadStatus(BaseModel):
    type: Annotated[Literal["systemError"], Field(title="SystemErrorThreadStatusType")]


class ActiveThreadStatus(BaseModel):
    activeFlags: list[Literal["waitingOnApproval", "waitingOnUserInput"]]
    type: Annotated[Literal["active"], Field(title="ActiveThreadStatusType")]


class TokenUsageBreakdown(BaseModel):
    cacheWriteInputTokens: int | None = 0
    cachedInputTokens: int
    inputTokens: int
    outputTokens: int
    reasoningOutputTokens: int
    totalTokens: int


class TurnInterruptParams(BaseModel):
    threadId: str
    turnId: str


class TurnInterruptResponse(BaseModel):
    pass


class TextUserInput(BaseModel):
    text: str
    text_elements: Annotated[list[TextElement] | None, Field(validate_default=True)] = []
    type: Annotated[Literal["text"], Field(title="TextUserInputType")]


class ImageUserInput(BaseModel):
    detail: Literal["auto", "low", "high", "original"] | None = None
    type: Annotated[Literal["image"], Field(title="ImageUserInputType")]
    url: str


class LocalImageUserInput(BaseModel):
    detail: Literal["auto", "low", "high", "original"] | None = None
    path: str
    type: Annotated[Literal["localImage"], Field(title="LocalImageUserInputType")]


class AudioUserInput(BaseModel):
    type: Annotated[Literal["audio"], Field(title="AudioUserInputType")]
    url: str


class LocalAudioUserInput(BaseModel):
    path: str
    type: Annotated[Literal["localAudio"], Field(title="LocalAudioUserInputType")]


class SkillUserInput(BaseModel):
    name: str
    path: str
    type: Annotated[Literal["skill"], Field(title="SkillUserInputType")]


class MentionUserInput(BaseModel):
    name: str
    path: str
    type: Annotated[Literal["mention"], Field(title="MentionUserInputType")]


class SearchWebSearchAction(BaseModel):
    queries: list[str] | None = None
    query: str | None = None
    type: Annotated[Literal["search"], Field(title="SearchWebSearchActionType")]


class OpenPageWebSearchAction(BaseModel):
    type: Annotated[Literal["openPage"], Field(title="OpenPageWebSearchActionType")]
    url: str | None = None


class FindInPageWebSearchAction(BaseModel):
    pattern: str | None = None
    type: Annotated[Literal["findInPage"], Field(title="FindInPageWebSearchActionType")]
    url: str | None = None


class OtherWebSearchAction(BaseModel):
    type: Annotated[Literal["other"], Field(title="OtherWebSearchActionType")]


class InitializeResponse(BaseModel):
    codexHome: str
    platformFamily: str
    platformOs: str
    userAgent: str


class NetworkApprovalContext(BaseModel):
    host: str
    protocol: Literal["http", "https", "socks5Tcp", "socks5Udp"]


class NetworkPolicyAmendment(BaseModel):
    action: Literal["allow", "deny"]
    host: str


class ChatgptAccount(BaseModel):
    email: str | None
    planType: Literal[
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "team",
        "self_serve_business_usage_based",
        "business",
        "enterprise_cbp_usage_based",
        "enterprise",
        "edu",
        "unknown",
    ]
    type: Annotated[Literal["chatgpt"], Field(title="ChatgptAccountType")]


class ActiveTurnNotSteerable(BaseModel):
    turnKind: Literal["review", "compact"]


class ActiveTurnNotSteerableCodexErrorInfo(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    activeTurnNotSteerable: ActiveTurnNotSteerable


class CollabAgentState(BaseModel):
    message: str | None = None
    status: Literal[
        "pendingInit", "running", "interrupted", "completed", "errored", "shutdown", "notFound"
    ]


class FileUpdateChange(BaseModel):
    diff: str
    kind: AddPatchChangeKind | DeletePatchChangeKind | UpdatePatchChangeKind
    path: str


class GetAccountResponse(BaseModel):
    account: ApiKeyAccount | ChatgptAccount | AmazonBedrockAccount | None = None
    requiresOpenaiAuth: bool


class MemoryCitation(BaseModel):
    entries: list[MemoryCitationEntry]
    threadIds: list[str]


class Model1(BaseModel):
    additionalSpeedTiers: list[str] | None = []
    availabilityNux: ModelAvailabilityNux | None = None
    defaultReasoningEffort: Annotated[str, Field(min_length=1)]
    defaultServiceTier: str | None = None
    displayName: str
    hidden: bool
    id: str
    inputModalities: list[Literal["text"] | Literal["image"] | Literal["audio"]] | None = [
        "text",
        "image",
    ]
    isDefault: bool
    model: str
    serviceTiers: Annotated[list[ModelServiceTier] | None, Field(validate_default=True)] = []
    supportedReasoningEfforts: list[ReasoningEffortOption]
    supportsPersonality: bool | None = False
    upgrade: str | None = None
    upgradeInfo: ModelUpgradeInfo | None = None


class ModelListResponse(BaseModel):
    data: list[Model1]
    nextCursor: str | None = None


class ThreadSpawn(BaseModel):
    agent_nickname: str | None = None
    agent_path: str | None = None
    agent_role: str | None = None
    depth: int
    parent_thread_id: str


class ThreadSpawnSubAgentSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    thread_spawn: ThreadSpawn


class UserMessageThreadItem(BaseModel):
    clientId: str | None = None
    content: list[
        TextUserInput
        | ImageUserInput
        | LocalImageUserInput
        | AudioUserInput
        | LocalAudioUserInput
        | SkillUserInput
        | MentionUserInput
    ]
    id: str
    type: Annotated[Literal["userMessage"], Field(title="UserMessageThreadItemType")]


class AgentMessageThreadItem(BaseModel):
    id: str
    memoryCitation: MemoryCitation | None = None
    phase: Literal["commentary"] | Literal["final_answer"] | None = None
    text: str
    type: Annotated[Literal["agentMessage"], Field(title="AgentMessageThreadItemType")]


class FileChangeThreadItem(BaseModel):
    changes: list[FileUpdateChange]
    id: str
    status: Literal["inProgress", "completed", "failed", "declined"]
    type: Annotated[Literal["fileChange"], Field(title="FileChangeThreadItemType")]


class CollabAgentToolCallThreadItem(BaseModel):
    agentsStates: dict[str, CollabAgentState]
    id: str
    model: str | None = None
    prompt: str | None = None
    reasoningEffort: ReasoningEffort | None = None
    receiverThreadIds: list[str]
    senderThreadId: str
    status: Literal["inProgress", "completed", "failed"]
    tool: Literal["spawnAgent", "sendInput", "resumeAgent", "wait", "closeAgent"]
    type: Annotated[
        Literal["collabAgentToolCall"], Field(title="CollabAgentToolCallThreadItemType")
    ]


class WebSearchThreadItem(BaseModel):
    action: (
        SearchWebSearchAction
        | OpenPageWebSearchAction
        | FindInPageWebSearchAction
        | OtherWebSearchAction
        | None
    ) = None
    id: str
    query: str
    results: list[Any] | None = None
    type: Annotated[Literal["webSearch"], Field(title="WebSearchThreadItemType")]


class ThreadStartParams(BaseModel):
    serviceName: str | None = None
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval | None = (
        None
    )
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"] | None = None
    baseInstructions: str | None = None
    config: dict[str, Any] | None = None
    cwd: str | None = None
    developerInstructions: str | None = None
    serviceTier: str | None = None
    personality: Literal["none", "friendly", "pragmatic"] | None = None
    ephemeral: bool | None = None
    threadSource: str | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    sessionStartSource: Literal["startup", "clear"] | None = None
    model: str | None = None
    modelProvider: str | None = None


class ThreadTokenUsage(BaseModel):
    last: TokenUsageBreakdown
    modelContextWindow: int | None = None
    total: TokenUsageBreakdown


class ThreadTokenUsageUpdatedNotification(BaseModel):
    threadId: str
    tokenUsage: ThreadTokenUsage
    turnId: str


class TurnError(BaseModel):
    additionalDetails: str | None = None
    codexErrorInfo: (
        Literal[
            "contextWindowExceeded",
            "sessionBudgetExceeded",
            "usageLimitExceeded",
            "serverOverloaded",
            "cyberPolicy",
            "internalServerError",
            "unauthorized",
            "badRequest",
            "threadRollbackFailed",
            "sandboxError",
            "other",
        ]
        | HttpConnectionFailedCodexErrorInfo
        | ResponseStreamConnectionFailedCodexErrorInfo
        | ResponseStreamDisconnectedCodexErrorInfo
        | ResponseTooManyFailedAttemptsCodexErrorInfo
        | ActiveTurnNotSteerableCodexErrorInfo
        | None
    ) = None
    message: str


class TurnPlanStep(BaseModel):
    status: Literal["pending", "inProgress", "completed"]
    step: str


class TurnPlanUpdatedNotification(BaseModel):
    explanation: str | None = None
    plan: list[TurnPlanStep]
    threadId: str
    turnId: str


class TurnStartParams(BaseModel):
    summary: Literal["auto", "concise", "detailed"] | Literal["none"] | None = None
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval | None = (
        None
    )
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"] | None = None
    clientUserMessageId: str | None = None
    serviceTier: str | None = None
    cwd: str | None = None
    effort: ReasoningEffort | None = None
    sandboxPolicy: (
        DangerFullAccessSandboxPolicy
        | ReadOnlySandboxPolicy
        | ExternalSandboxSandboxPolicy
        | WorkspaceWriteSandboxPolicy
        | None
    ) = None
    input: list[
        TextUserInput
        | ImageUserInput
        | LocalImageUserInput
        | AudioUserInput
        | LocalAudioUserInput
        | SkillUserInput
        | MentionUserInput
    ]
    model: str | None = None
    threadId: str
    outputSchema: Any | None = None
    personality: Literal["none", "friendly", "pragmatic"] | None = None


class ApplyNetworkPolicyAmendment(BaseModel):
    network_policy_amendment: NetworkPolicyAmendment


class ApplyNetworkPolicyAmendmentCommandExecutionApprovalDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    applyNetworkPolicyAmendment: ApplyNetworkPolicyAmendment


class CommandExecutionRequestApprovalParams(BaseModel):
    threadId: str
    approvalId: str | None = None
    turnId: str
    command: str | None = None
    commandActions: (
        list[
            ReadCommandAction | ListFilesCommandAction | SearchCommandAction | UnknownCommandAction
        ]
        | None
    ) = None
    cwd: str | None = None
    environmentId: str | None = None
    itemId: str
    networkApprovalContext: NetworkApprovalContext | None = None
    proposedExecpolicyAmendment: list[str] | None = None
    proposedNetworkPolicyAmendments: list[NetworkPolicyAmendment] | None = None
    reason: str | None = None
    startedAtMs: int


class CommandExecutionRequestApprovalResponse(BaseModel):
    decision: (
        Literal["accept"]
        | Literal["acceptForSession"]
        | AcceptWithExecpolicyAmendmentCommandExecutionApprovalDecision
        | ApplyNetworkPolicyAmendmentCommandExecutionApprovalDecision
        | Literal["decline"]
        | Literal["cancel"]
    )


class ErrorNotification(BaseModel):
    error: TurnError
    threadId: str
    turnId: str
    willRetry: bool


class ItemCompletedNotification(BaseModel):
    completedAtMs: int
    item: (
        UserMessageThreadItem
        | HookPromptThreadItem
        | AgentMessageThreadItem
        | PlanThreadItem
        | ReasoningThreadItem
        | CommandExecutionThreadItem
        | FileChangeThreadItem
        | McpToolCallThreadItem
        | DynamicToolCallThreadItem
        | CollabAgentToolCallThreadItem
        | SubAgentActivityThreadItem
        | WebSearchThreadItem
        | ImageViewThreadItem
        | SleepThreadItem
        | ImageGenerationThreadItem
        | EnteredReviewModeThreadItem
        | ExitedReviewModeThreadItem
        | ContextCompactionThreadItem
    )
    threadId: str
    turnId: str


class ItemStartedNotification(BaseModel):
    item: (
        UserMessageThreadItem
        | HookPromptThreadItem
        | AgentMessageThreadItem
        | PlanThreadItem
        | ReasoningThreadItem
        | CommandExecutionThreadItem
        | FileChangeThreadItem
        | McpToolCallThreadItem
        | DynamicToolCallThreadItem
        | CollabAgentToolCallThreadItem
        | SubAgentActivityThreadItem
        | WebSearchThreadItem
        | ImageViewThreadItem
        | SleepThreadItem
        | ImageGenerationThreadItem
        | EnteredReviewModeThreadItem
        | ExitedReviewModeThreadItem
        | ContextCompactionThreadItem
    )
    startedAtMs: int
    threadId: str
    turnId: str


class SubAgentSessionSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    subAgent: (
        Literal["review", "compact", "memory_consolidation"]
        | ThreadSpawnSubAgentSource
        | OtherSubAgentSource
    )


class Turn(BaseModel):
    completedAt: int | None = None
    durationMs: int | None = None
    error: TurnError | None = None
    id: str
    items: list[
        UserMessageThreadItem
        | HookPromptThreadItem
        | AgentMessageThreadItem
        | PlanThreadItem
        | ReasoningThreadItem
        | CommandExecutionThreadItem
        | FileChangeThreadItem
        | McpToolCallThreadItem
        | DynamicToolCallThreadItem
        | CollabAgentToolCallThreadItem
        | SubAgentActivityThreadItem
        | WebSearchThreadItem
        | ImageViewThreadItem
        | SleepThreadItem
        | ImageGenerationThreadItem
        | EnteredReviewModeThreadItem
        | ExitedReviewModeThreadItem
        | ContextCompactionThreadItem
    ]
    itemsView: Literal["notLoaded"] | Literal["summary"] | Literal["full"] | None = "full"
    startedAt: int | None = None
    status: Literal["completed", "interrupted", "failed", "inProgress"]


class TurnCompletedNotification(BaseModel):
    threadId: str
    turn: Turn


class TurnStartResponse(BaseModel):
    turn: Turn


class TurnStartedNotification(BaseModel):
    threadId: str
    turn: Turn


class Thread(BaseModel):
    agentNickname: str | None = None
    agentRole: str | None = None
    threadSource: str | None = None
    cliVersion: str
    createdAt: int
    cwd: str
    ephemeral: bool
    turns: list[Turn]
    forkedFromId: str | None = None
    gitInfo: GitInfo | None = None
    updatedAt: int
    id: str
    modelProvider: str
    name: str | None = None
    parentThreadId: str | None = None
    path: str | None = None
    preview: str
    recencyAt: int | None = None
    sessionId: str
    source: (
        Literal["cli", "vscode", "exec", "appServer", "unknown"]
        | CustomSessionSource
        | SubAgentSessionSource
    )
    status: NotLoadedThreadStatus | IdleThreadStatus | SystemErrorThreadStatus | ActiveThreadStatus


class ThreadResumeResponse(BaseModel):
    thread: Thread
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"]
    cwd: str
    sandbox: (
        DangerFullAccessSandboxPolicy
        | ReadOnlySandboxPolicy
        | ExternalSandboxSandboxPolicy
        | WorkspaceWriteSandboxPolicy
    )
    instructionSources: list[str] | None = []
    reasoningEffort: ReasoningEffort | None = None
    model: str
    modelProvider: str
    serviceTier: str | None = None


class ThreadStartResponse(BaseModel):
    sandbox: (
        DangerFullAccessSandboxPolicy
        | ReadOnlySandboxPolicy
        | ExternalSandboxSandboxPolicy
        | WorkspaceWriteSandboxPolicy
    )
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"]
    cwd: str
    instructionSources: list[str] | None = []
    model: str
    modelProvider: str
    serviceTier: str | None = None
    reasoningEffort: ReasoningEffort | None = None
    thread: Thread


class ThreadStartedNotification(BaseModel):
    thread: Thread
