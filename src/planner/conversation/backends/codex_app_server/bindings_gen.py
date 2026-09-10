"""Codex app-server protocol bindings. Generated — do not edit.

Regenerate with:
    python -m planner.conversation.backends.codex_app_server.generate_bindings

The pin these models were generated under:

    codex binary version   codex-cli 0.153.3
    upstream openai/codex  rust-v0.153.3 = b1a547b1f73ce86205d9222ac19cff334b3b7a2e
    schema obtained by     codex app-server generate-json-schema --out <dir>
    from the dump's        codex_app_server_protocol.schemas.json
    dump digest (sha256)   90760ee89ab33a9795a8876ff16e0024f37a4086e3c6bd6673f8f634116fe2da
    pruned and vendored    schema/codex_app_server_protocol.subset.schema.json
    subset digest (sha256) 53b9bb7f8d8181a7ba9a20eb653564a33e9de8fa8d940190d818618fb00d07d7
    definitions generated  177

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
    extensions: dict[str, Any] | None = None
    mcpServerOpenaiFormElicitation: bool | None = None
    optOutNotificationMethods: list[str] | None = None
    requestAttestation: bool | None = False


class InitializeParams(BaseModel):
    capabilities: InitializeCapabilities | None = None
    clientInfo: ClientInfo


class ToolRequestUserInputAnswer(BaseModel):
    answers: list[str]


class ToolRequestUserInputOption(BaseModel):
    description: str
    label: str


class ToolRequestUserInputQuestion(BaseModel):
    header: str
    id: str
    isOther: bool | None = False
    isSecret: bool | None = False
    options: list[ToolRequestUserInputOption] | None = None
    question: str


class ToolRequestUserInputResponse(BaseModel):
    answers: dict[str, ToolRequestUserInputAnswer]


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


class AppBranding(BaseModel):
    category: str | None = None
    developer: str | None = None
    isDiscoverableApp: bool
    privacyPolicy: str | None = None
    termsOfService: str | None = None
    website: str | None = None


class AppReview(BaseModel):
    status: str


class AppScreenshot(BaseModel):
    fileId: str | None = None
    url: str | None = None
    userPrompt: str


class AppsInstalledParams(BaseModel):
    forceRefresh: bool | None = None
    threadId: str | None = None


class AppsListParams(BaseModel):
    cursor: str | None = None
    forceRefetch: bool | None = None
    limit: Annotated[int | None, Field(ge=0)] = None
    threadId: str | None = None


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


class AsyncUserInputQuestion(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    options: list[str] | None = None
    title: str


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


class InputTextFunctionCallOutputContentItem(BaseModel):
    text: str
    type: Annotated[
        Literal["input_text"], Field(title="InputTextFunctionCallOutputContentItemType")
    ]


class InputAudioFunctionCallOutputContentItem(BaseModel):
    audio_url: str
    type: Annotated[
        Literal["input_audio"], Field(title="InputAudioFunctionCallOutputContentItemType")
    ]


class EncryptedContentFunctionCallOutputContentItem(BaseModel):
    encrypted_content: str
    type: Annotated[
        Literal["encrypted_content"],
        Field(title="EncryptedContentFunctionCallOutputContentItemType"),
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


class UsageLimitExceededImageGenerationFailure(BaseModel):
    limitId: str
    resetsAt: int | None = None
    type: Annotated[
        Literal["usageLimitExceeded"], Field(title="UsageLimitExceededImageGenerationFailureType")
    ]


class ImageGenerationFailure(RootModel[UsageLimitExceededImageGenerationFailure]):
    root: UsageLimitExceededImageGenerationFailure


class InstalledApp(BaseModel):
    callable: bool
    enabled: bool
    id: str
    runtimeName: str | None = None


class MarketplaceInterface(BaseModel):
    displayName: str | None = None


class MarketplaceLoadErrorInfo(BaseModel):
    marketplacePath: str
    message: str


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


class MisalignmentSteer(BaseModel):
    message: str


class ModelAvailabilityNux(BaseModel):
    message: str


class ModelListParams(BaseModel):
    cursor: str | None = None
    includeHidden: bool | None = None
    limit: Annotated[int | None, Field(ge=0)] = None


class ModelServiceTier(BaseModel):
    description: str
    id: str
    name: str


class ModelUpgradeInfo(BaseModel):
    migrationMarkdown: str | None = None
    model: str
    modelLink: str | None = None
    retirementAt: int | None = None
    upgradeCopy: str | None = None


class AddPatchChangeKind(BaseModel):
    type: Annotated[Literal["add"], Field(title="AddPatchChangeKindType")]


class DeletePatchChangeKind(BaseModel):
    type: Annotated[Literal["delete"], Field(title="DeletePatchChangeKindType")]


class UpdatePatchChangeKind(BaseModel):
    move_path: str | None = None
    type: Annotated[Literal["update"], Field(title="UpdatePatchChangeKindType")]


class PluginInstalledParams(BaseModel):
    cwds: list[str] | None = None
    installSuggestionPluginNames: list[str] | None = None


class PluginInterface(BaseModel):
    brandColor: str | None = None
    capabilities: list[str]
    category: str | None = None
    composerIcon: str | None = None
    composerIconUrl: str | None = None
    defaultPrompt: list[str] | None = None
    developerName: str | None = None
    displayName: str | None = None
    logo: str | None = None
    logoDark: str | None = None
    logoUrl: str | None = None
    logoUrlDark: str | None = None
    longDescription: str | None = None
    privacyPolicyUrl: str | None = None
    screenshotUrls: list[str]
    screenshots: list[str]
    shortDescription: str | None = None
    termsOfServiceUrl: str | None = None
    websiteUrl: str | None = None


class LocalPluginSource(BaseModel):
    path: str
    type: Annotated[Literal["local"], Field(title="LocalPluginSourceType")]


class GitPluginSource(BaseModel):
    path: str | None = None
    refName: str | None = None
    sha: str | None = None
    type: Annotated[Literal["git"], Field(title="GitPluginSourceType")]
    url: str


class NpmPluginSource(BaseModel):
    package: str
    registry: str | None = None
    type: Annotated[Literal["npm"], Field(title="NpmPluginSourceType")]
    version: str | None = None


class RemotePluginSource(BaseModel):
    type: Annotated[Literal["remote"], Field(title="RemotePluginSourceType")]


class ReasoningEffort(RootModel[str]):
    root: Annotated[str, Field(min_length=1)]


class ReasoningEffortOption(BaseModel):
    description: str
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


class UncommittedChangesReviewTarget(BaseModel):
    type: Annotated[
        Literal["uncommittedChanges"], Field(title="UncommittedChangesReviewTargetType")
    ]


class BaseBranchReviewTarget(BaseModel):
    branch: str
    type: Annotated[Literal["baseBranch"], Field(title="BaseBranchReviewTargetType")]


class CommitReviewTarget(BaseModel):
    sha: str
    title: str | None = None
    type: Annotated[Literal["commit"], Field(title="CommitReviewTargetType")]


class CustomReviewTarget(BaseModel):
    instructions: str
    type: Annotated[Literal["custom"], Field(title="CustomReviewTargetType")]


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


class SkillErrorInfo(BaseModel):
    message: str
    path: str


class SkillInterface(BaseModel):
    brandColor: str | None = None
    defaultPrompt: str | None = None
    displayName: str | None = None
    iconLarge: str | None = None
    iconLargeUrl: str | None = None
    iconSmall: str | None = None
    iconSmallUrl: str | None = None
    shortDescription: str | None = None


class SkillToolDependency(BaseModel):
    command: str | None = None
    description: str | None = None
    transport: str | None = None
    type: str
    url: str | None = None
    value: str


class SkillsChangedNotification(BaseModel):
    pass


class SkillsListParams(BaseModel):
    cwds: list[str] | None = None
    forceReload: bool | None = None


class OtherSubAgentSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    other: str


class TextElement(BaseModel):
    byteRange: ByteRange
    placeholder: str | None = None


class ThreadCompactStartParams(BaseModel):
    threadId: str


class ThreadCompactStartResponse(BaseModel):
    pass


class ThreadGoalClearParams(BaseModel):
    threadId: str


class ThreadGoalClearResponse(BaseModel):
    cleared: bool


class ThreadGoalGetParams(BaseModel):
    threadId: str


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


class McpToolCallThreadItem(BaseModel):
    appContext: McpToolCallAppContext | None = None
    arguments: Any
    durationMs: int | None = None
    error: McpToolCallError | None = None
    id: str
    mcpAppResourceUri: str | None = None
    pluginId: str | None = None
    readOnlyHint: bool | None = None
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
    kind: Literal["started", "interacted", "interrupted", "completed"]
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
    failure: ImageGenerationFailure | None = None
    id: str
    result: str
    revisedPrompt: str | None = None
    savedPath: str | None = None
    status: str
    transparentBackground: bool | None = None
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
    excludeTurns: bool | None = None
    model: str | None = None
    modelProvider: str | None = None
    personality: Literal["none", "friendly", "pragmatic"] | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    serviceTier: str | None = None
    threadId: str


class ThreadSectionAppearance(BaseModel):
    color: str | None = None
    icon: str | None = None


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


class TurnSteerResponse(BaseModel):
    turnId: str


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


class ToolRequestUserInputParams(BaseModel):
    autoResolutionMs: Annotated[int | None, Field(ge=0)] = None
    isBlocking: bool
    itemId: str
    questions: list[ToolRequestUserInputQuestion]
    threadId: str
    turnId: str


class ChatgptAccount(BaseModel):
    email: str | None
    planType: Literal[
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "team",
        "self_serve_business_prolite",
        "self_serve_business_usage_based",
        "business",
        "ent26",
        "enterprise_cbp_automation",
        "enterprise_cbp_usage_based",
        "enterprise",
        "edu",
        "edu_plus",
        "edu_pro",
        "unknown",
    ]
    type: Annotated[Literal["chatgpt"], Field(title="ChatgptAccountType")]


class AppMetadata(BaseModel):
    categories: list[str] | None = None
    developer: str | None = None
    firstPartyRequiresInstall: bool | None = None
    review: AppReview | None = None
    screenshots: list[AppScreenshot] | None = None
    seoDescription: str | None = None
    showInComposerWhenUnlinked: bool | None = None
    subCategories: list[str] | None = None
    version: str | None = None
    versionId: str | None = None
    versionNotes: str | None = None


class AppsInstalledResponse(BaseModel):
    apps: list[InstalledApp]


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


class ReadCommandAction(BaseModel):
    command: str
    name: str
    path: str
    type: Annotated[Literal["read"], Field(title="ReadCommandActionType")]


class FileUpdateChange(BaseModel):
    diff: str
    kind: AddPatchChangeKind | DeletePatchChangeKind | UpdatePatchChangeKind
    path: str


class InputImageFunctionCallOutputContentItem(BaseModel):
    detail: Literal["auto", "low", "high", "original"] | None = None
    image_url: str
    type: Annotated[
        Literal["input_image"], Field(title="InputImageFunctionCallOutputContentItemType")
    ]


class GetAccountResponse(BaseModel):
    account: ApiKeyAccount | ChatgptAccount | AmazonBedrockAccount | None = None
    requiresOpenaiAuth: bool


class MemoryCitation(BaseModel):
    entries: list[MemoryCitationEntry]
    threadIds: list[str]


class MisalignmentErrorDetails(BaseModel):
    detailedExplanation: str | None = None
    errorType: str | None = None
    steer: MisalignmentSteer | None = None


class Model1(BaseModel):
    additionalSpeedTiers: list[str] | None = []
    availabilityNux: ModelAvailabilityNux | None = None
    defaultReasoningEffort: Annotated[str, Field(min_length=1)]
    defaultServiceTier: str | None = None
    description: str
    displayName: str
    hidden: bool
    id: str
    inputModalities: list[Literal["text"] | Literal["image"] | Literal["audio"]] | None = [
        "text",
        "image",
    ]
    isDefault: bool
    model: str
    modelSpecialty: str | None = None
    multiAgentVersion: Literal["disabled", "v1", "v2"] | None = None
    serviceTiers: Annotated[list[ModelServiceTier] | None, Field(validate_default=True)] = []
    supportedReasoningEfforts: list[ReasoningEffortOption]
    supportsPersonality: bool | None = False
    upgrade: str | None = None
    upgradeInfo: ModelUpgradeInfo | None = None


class ModelListResponse(BaseModel):
    data: list[Model1]
    nextCursor: str | None = None


class PluginSharePrincipal(BaseModel):
    name: str
    principalId: str
    principalType: Literal["user", "group", "workspace"]
    role: Literal["reader", "editor", "owner"]


class ReviewStartParams(BaseModel):
    delivery: Literal["inline", "detached"] | None = None
    target: (
        UncommittedChangesReviewTarget
        | BaseBranchReviewTarget
        | CommitReviewTarget
        | CustomReviewTarget
    )
    threadId: str


class SkillDependencies(BaseModel):
    tools: list[SkillToolDependency]


class SkillMetadata(BaseModel):
    dependencies: SkillDependencies | None = None
    description: str
    enabled: bool
    interface: SkillInterface | None = None
    name: str
    path: str
    pluginId: str | None = None
    scope: Literal["user", "repo", "system", "admin"]
    shortDescription: str | None = None


class SkillsListEntry(BaseModel):
    cwd: str
    errors: list[SkillErrorInfo]
    skills: list[SkillMetadata]


class SkillsListResponse(BaseModel):
    data: list[SkillsListEntry]


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


class ThreadGoal(BaseModel):
    createdAt: int
    objective: str
    status: Literal["active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"]
    threadId: str
    timeUsedSeconds: int
    tokenBudget: int | None = None
    tokensUsed: int
    updatedAt: int


class ThreadGoalGetResponse(BaseModel):
    goal: ThreadGoal | None = None


class ThreadGoalSetParams(BaseModel):
    objective: str | None = None
    status: (
        Literal["active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"] | None
    ) = None
    threadId: str
    tokenBudget: int | None = None


class ThreadGoalSetResponse(BaseModel):
    goal: ThreadGoal


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
    delivery: Literal["async"] | None = None
    id: str
    memoryCitation: MemoryCitation | None = None
    phase: Literal["commentary"] | Literal["final_answer"] | None = None
    questions: list[AsyncUserInputQuestion] | None = None
    text: str
    type: Annotated[Literal["agentMessage"], Field(title="AgentMessageThreadItemType")]


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
    pluginId: str | None = None
    processId: str | None = None
    scriptPath: str | None = None
    source: Literal["agent", "userShell", "unifiedExecStartup", "unifiedExecInteraction"] | None = (
        "agent"
    )
    status: Literal["inProgress", "completed", "failed", "declined"]
    type: Annotated[Literal["commandExecution"], Field(title="CommandExecutionThreadItemType")]


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
    status: Literal["inProgress", "completed", "failed", "interrupted"]
    tool: Literal[
        "spawnAgent",
        "sendInput",
        "resumeAgent",
        "wait",
        "closeAgent",
        "sendMessage",
        "followupTask",
        "interruptAgent",
        "listAgents",
    ]
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


class ThreadSection(BaseModel):
    appearance: ThreadSectionAppearance | None = None
    id: str
    name: str


class ThreadStartParams(BaseModel):
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval | None = (
        None
    )
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"] | None = None
    baseInstructions: str | None = None
    config: dict[str, Any] | None = None
    cwd: str | None = None
    developerInstructions: str | None = None
    ephemeral: bool | None = None
    model: str | None = None
    modelProvider: str | None = None
    personality: Literal["none", "friendly", "pragmatic"] | None = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    serviceName: str | None = None
    serviceTier: str | None = None
    sessionStartSource: Literal["startup", "clear"] | None = None
    threadSource: str | None = None


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
            "rateLimitExceeded",
            "serverOverloaded",
            "cyberPolicy",
            "misalignmentPolicyViolation",
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
    misalignment: MisalignmentErrorDetails | None = None


class TurnPlanStep(BaseModel):
    status: Literal["pending", "inProgress", "completed"]
    step: str


class TurnPlanUpdatedNotification(BaseModel):
    explanation: str | None = None
    plan: list[TurnPlanStep]
    threadId: str
    turnId: str


class TurnSteerParams(BaseModel):
    clientUserMessageId: str | None = None
    expectedTurnId: str
    input: list[
        TextUserInput
        | ImageUserInput
        | LocalImageUserInput
        | AudioUserInput
        | LocalAudioUserInput
        | SkillUserInput
        | MentionUserInput
    ]
    threadId: str


class ApplyNetworkPolicyAmendment(BaseModel):
    network_policy_amendment: NetworkPolicyAmendment


class ApplyNetworkPolicyAmendmentCommandExecutionApprovalDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    applyNetworkPolicyAmendment: ApplyNetworkPolicyAmendment


class CommandExecutionRequestApprovalParams(BaseModel):
    approvalId: str | None = None
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
    kind: Literal["command", "writeStdin"] | None = "command"
    networkApprovalContext: NetworkApprovalContext | None = None
    proposedExecpolicyAmendment: list[str] | None = None
    proposedNetworkPolicyAmendments: list[NetworkPolicyAmendment] | None = None
    reason: str | None = None
    startedAtMs: int
    threadId: str
    turnId: str


class CommandExecutionRequestApprovalResponse(BaseModel):
    decision: (
        Literal["accept"]
        | Literal["acceptForSession"]
        | AcceptWithExecpolicyAmendmentCommandExecutionApprovalDecision
        | ApplyNetworkPolicyAmendmentCommandExecutionApprovalDecision
        | Literal["decline"]
        | Literal["cancel"]
    )


class AppInfo(BaseModel):
    appMetadata: AppMetadata | None = None
    branding: AppBranding | None = None
    description: str | None = None
    distributionChannel: str | None = None
    iconAssets: dict[str, Any] | None = None
    iconDarkAssets: dict[str, Any] | None = None
    id: str
    installUrl: str | None = None
    isAccessible: bool | None = False
    isEnabled: bool | None = True
    labels: dict[str, Any] | None = None
    logoUrl: str | None = None
    logoUrlDark: str | None = None
    name: str
    pluginDisplayNames: list[str] | None = []


class AppListUpdatedNotification(BaseModel):
    data: list[AppInfo]


class AppsListResponse(BaseModel):
    data: list[AppInfo]
    nextCursor: str | None = None


class ErrorNotification(BaseModel):
    error: TurnError
    threadId: str
    turnId: str
    willRetry: bool


class PluginShareContext(BaseModel):
    canPublishToWorkspace: bool | None = None
    creatorAccountUserId: str | None = None
    creatorName: str | None = None
    discoverability: Literal["LISTED", "UNLISTED", "PRIVATE"] | None = None
    remotePluginId: str
    remoteVersion: str | None = None
    sharePrincipals: list[PluginSharePrincipal] | None = None
    shareUrl: str | None = None


class PluginSummary(BaseModel):
    authPolicy: Literal["ON_INSTALL", "ON_USE"]
    availability: Literal["DISABLED_BY_ADMIN"] | Literal["AVAILABLE"] | None = "AVAILABLE"
    disabledReason: (
        Literal["disabled_by_admin", "plan_not_eligible", "required_app_unavailable", "unknown"]
        | None
    ) = None
    eligiblePlanTypes: list[str] | None = None
    enabled: bool
    id: str
    installPolicy: Literal["NOT_AVAILABLE", "AVAILABLE", "INSTALLED_BY_DEFAULT"]
    installPolicySource: Literal["WORKSPACE_SETTING", "IMPLICIT_CANONICAL_APP"] | None = None
    installed: bool
    installedAt: int | None = None
    interface: PluginInterface | None = None
    keywords: list[str] | None = []
    localVersion: str | None = None
    mustShowInstallationInterstitial: bool | None = None
    name: str
    remotePluginId: str | None = None
    shareContext: PluginShareContext | None = None
    source: LocalPluginSource | GitPluginSource | NpmPluginSource | RemotePluginSource
    version: str | None = None


class SubAgentSessionSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    subAgent: (
        Literal["review", "compact", "memory_consolidation"]
        | ThreadSpawnSubAgentSource
        | OtherSubAgentSource
    )


class FunctionCallOutputThreadItem(BaseModel):
    id: str
    name: str
    namespace: str | None = None
    output: (
        str
        | list[
            InputTextFunctionCallOutputContentItem
            | InputImageFunctionCallOutputContentItem
            | InputAudioFunctionCallOutputContentItem
            | EncryptedContentFunctionCallOutputContentItem
        ]
    )
    type: Annotated[Literal["functionCallOutput"], Field(title="FunctionCallOutputThreadItemType")]


class Turn(BaseModel):
    completedAt: int | None = None
    durationMs: int | None = None
    error: TurnError | None = None
    id: str
    items: list[
        UserMessageThreadItem
        | HookPromptThreadItem
        | AgentMessageThreadItem
        | FunctionCallOutputThreadItem
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


class TurnToolOutput(BaseModel):
    name: str
    namespace: str | None = None
    output: (
        str
        | list[
            InputTextFunctionCallOutputContentItem
            | InputImageFunctionCallOutputContentItem
            | InputAudioFunctionCallOutputContentItem
            | EncryptedContentFunctionCallOutputContentItem
        ]
    )


class ItemCompletedNotification(BaseModel):
    completedAtMs: int
    item: (
        UserMessageThreadItem
        | HookPromptThreadItem
        | AgentMessageThreadItem
        | FunctionCallOutputThreadItem
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
        | FunctionCallOutputThreadItem
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


class PluginMarketplaceEntry(BaseModel):
    interface: MarketplaceInterface | None = None
    name: str
    path: str | None = None
    plugins: list[PluginSummary]


class ReviewStartResponse(BaseModel):
    reviewThreadId: str
    turn: Turn


class Thread(BaseModel):
    agentNickname: str | None = None
    agentRole: str | None = None
    cliVersion: str
    createdAt: int
    cwd: str
    ephemeral: bool
    forkedFromId: str | None = None
    gitInfo: GitInfo | None = None
    historyMode: Literal["legacy", "paginated"] | None = "legacy"
    id: str
    model: str | None = None
    modelProvider: str
    name: str | None = None
    parentThreadId: str | None = None
    path: str | None = None
    preview: str
    projectId: str | None
    reasoningEffort: ReasoningEffort | None = None
    recencyAt: int | None = None
    section: ThreadSection | None = None
    sectionEnteredAt: int | None = None
    sessionId: str
    source: (
        Literal["cli", "vscode", "exec", "appServer", "unknown"]
        | CustomSessionSource
        | SubAgentSessionSource
    )
    status: NotLoadedThreadStatus | IdleThreadStatus | SystemErrorThreadStatus | ActiveThreadStatus
    threadSource: str | None = None
    turns: list[Turn]
    updatedAt: int


class ThreadResumeResponse(BaseModel):
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"]
    cwd: str
    instructionSources: list[str] | None = []
    itemsBackwardsCursor: str | None = None
    model: str
    modelProvider: str
    reasoningEffort: ReasoningEffort | None = None
    sandbox: (
        DangerFullAccessSandboxPolicy
        | ReadOnlySandboxPolicy
        | ExternalSandboxSandboxPolicy
        | WorkspaceWriteSandboxPolicy
    )
    serviceTier: str | None = None
    thread: Thread
    turnsBackwardsCursor: str | None = None


class ThreadStartResponse(BaseModel):
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"]
    cwd: str
    instructionSources: list[str] | None = []
    model: str
    modelProvider: str
    reasoningEffort: ReasoningEffort | None = None
    sandbox: (
        DangerFullAccessSandboxPolicy
        | ReadOnlySandboxPolicy
        | ExternalSandboxSandboxPolicy
        | WorkspaceWriteSandboxPolicy
    )
    serviceTier: str | None = None
    thread: Thread


class ThreadStartedNotification(BaseModel):
    thread: Thread


class TurnStartParams(BaseModel):
    approvalPolicy: Literal["untrusted", "on-request", "never"] | GranularAskForApproval | None = (
        None
    )
    approvalsReviewer: Literal["user", "auto_review", "guardian_subagent"] | None = None
    clientUserMessageId: str | None = None
    cwd: str | None = None
    effort: ReasoningEffort | None = None
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
    outputSchema: Any | None = None
    personality: Literal["none", "friendly", "pragmatic"] | None = None
    sandboxPolicy: (
        DangerFullAccessSandboxPolicy
        | ReadOnlySandboxPolicy
        | ExternalSandboxSandboxPolicy
        | WorkspaceWriteSandboxPolicy
        | None
    ) = None
    serviceTier: str | None = None
    serviceTierForTurn: str | None = None
    summary: Literal["auto", "concise", "detailed"] | Literal["none"] | None = None
    threadId: str
    toolOutput: TurnToolOutput | None = None
    turnTrigger: str | None = None


class PluginInstalledResponse(BaseModel):
    marketplaceLoadErrors: Annotated[
        list[MarketplaceLoadErrorInfo] | None, Field(validate_default=True)
    ] = []
    marketplaces: list[PluginMarketplaceEntry]
