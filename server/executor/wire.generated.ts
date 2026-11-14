// Generated from wire.schema.json by tools/generate_wire.ts. Do not edit.
// Required nullable fields stay required. Defaults are optional on the wire.
import wire_schema from './wire.schema.json' with { type: 'json' };
export const WIRE_SCHEMA = wire_schema;
export const PROTOCOL_VERSION = 22;
export const SPOOL_FORMAT_VERSION = 5;
export const COMMAND_JOURNAL_FORMAT_VERSION = 1;
export type AcceptanceReceipt = {
  accepted_at_vps_ms: number;
  disposition: ReceiptDisposition;
  result: JsonObject;
  sender_id: string;
  submission_id: string;
};
export type AgentMessageCompleted = { content: MessageContent };
export type AgentMessageDelta = { text_delta: string };
export type AnswerApplication = { answer_id: string; applied_at_mac_ms: number; ask: AskReference };
export type AnswerPermission = {
  answer?: DurableAnswer | null;
  ask_id: string;
  kind?: 'answer_permission_ask';
  option_id: string;
};
export type AnswerUserInput = {
  answer?: DurableAnswer | null;
  answers: UserInputAnswer[];
  kind?: 'answer_user_input';
  request_id: string;
};
export type AskKind = 'permission' | 'user_input';
export type AskReference = {
  ask_id: string;
  child: ChildReference;
  kind: AskKind;
  turn: TurnToken;
};
export type BackendEvent = {
  arguments: JsonObject;
  child: ChildReference;
  method: BackendEventMethod;
  turn: TurnToken | null;
};
export type BackendEventMethod =
  | 'agent_message_delta'
  | 'model_thinking_happened'
  | 'agent_message_completed'
  | 'command_reply_reported'
  | 'tool_call_started'
  | 'tool_call_progress'
  | 'tool_call_finished'
  | 'plan_updated'
  | 'token_usage_reported'
  | 'context_compacted'
  | 'permission_ask_raised'
  | 'user_input_requested'
  | 'user_input_failed'
  | 'turn_ended'
  | 'vendor_session_cursor_rebound'
  | 'composer_catalog_reported'
  | 'background_task_started'
  | 'background_task_ended'
  | 'backend_stream_ended';
export type BackendIdentity = {
  account_label?: string | null;
  detail?: string | null;
  login_command?: string | null;
  status: BackendIdentityStatus;
};
export type BackendIdentityStatus = 'authenticated' | 'unauthenticated' | 'unknown';
export type BackendInstallMethod =
  'npm_global' | 'bun_global' | 'pnpm_global' | 'homebrew' | 'native' | 'manual_only';
export type BackendModel = {
  detail?: string | null;
  display_name?: string | null;
  model_id: string;
  reasoning_effort_options?: string[] | null;
  resolved_model_id?: string | null;
  selected_variant?: string | null;
  version?: number[];
};
export type BackendPermissionAsk = {
  ask_id: string;
  detail: string | null;
  options: PermissionAskOption[];
  title: string;
};
export type BackendSnapshot = {
  available_models: BackendModel[];
  backend_key: ConversationBackendKey;
  default_model_id: string | null;
  default_reasoning_effort: string | null;
  diagnoses: string[];
  executable_path: string | null;
  identity: BackendIdentity | null;
  installed: boolean;
  models_complete?: boolean;
  reasoning_effort_options: string[];
  update_advisory: BackendUpdateAdvisory | null;
  version: string | null;
};
export type BackendStreamEnded = object;
export type BackendUpdateAdvisory = {
  detail: string;
  install_method: BackendInstallMethod;
  latest_version: string | null;
  update_available: boolean;
  update_command: string[] | null;
};
export type BackendUserInputRequest = { questions: UserInputQuestion[]; request_id: string };
export type BackgroundTaskEnded = { task_id: string; task_status: string };
export type BackgroundTaskStarted = { task_description: string; task_id: string };
export type CancelTurn = { kind?: 'cancel_running_turn' };
export type CanonicalSkillSnapshot = {
  required_skill_names: string[];
  sources: CanonicalSkillSource[];
};
export type CanonicalSkillSource = { skill_name: string; source_text: string };
export type ChildCommand = {
  arguments: ChildCommandArguments;
  child: ChildReference;
  command_id: string;
  file_dependencies?: FileManifest[];
  operation: ChildOperation;
  turn: TurnToken | null;
};
export type ChildCommandArguments = (
  StartRun | WritePrompt | Steer | CancelTurn | AnswerPermission | AnswerUserInput | StopRun
) & { kind: string };
export type ChildOperation =
  | 'start'
  | 'write_prompt'
  | 'steer'
  | 'cancel_running_turn'
  | 'answer_permission_ask'
  | 'answer_user_input'
  | 'stop';
export type ChildReference = {
  child_id: string;
  conversation_id: string;
  executor_incarnation: string;
};
export type CliWrite = {
  arguments: JsonObject;
  child: ChildReference;
  operation: string;
  turn: TurnToken | null;
};
export type CommandDisposition = 'succeeded' | 'refused' | 'uncertain';
export type CommandOutcome = {
  child: ChildReference;
  command_id: string;
  disposition: CommandDisposition;
  result: JsonObject;
};
export type CommandOutcomeReply = { recorded: string[]; rejected?: RejectedCommandResult[] };
export type CommandOutcomeReport = { outcomes: CommandResult[] };
export type CommandReplyReported = { reply_text: string };
export type CommandResult = {
  child_outcome?: CommandOutcome | null;
  command_id: string;
  model_list?: JsonObject | null;
};
export type ComposerCatalogEntry = {
  argument_hint?: string | null;
  description: string;
  display_text: string;
  insertion_text: string;
  kind: ComposerCatalogEntryKind;
};
export type ComposerCatalogEntryKind = 'command' | 'skill' | 'app' | 'plugin';
export type ComposerCatalogReported = { composer_catalog: ComposerCatalogEntry[] };
export type ContextCompacted = object;
export type ConversationAccess = 'full';
export type ConversationBackendKey = 'hermes' | 'codex' | 'claude';
export type ConversationRoleMaterials = {
  identity_environment_variables?: [string, string][];
  role_text: string;
};
export type ConversationTurnEnding = 'completed' | 'failed' | 'interrupted';
export type DurableAnswer = {
  accepted_at_vps_ms: number;
  answer_id: string;
  ask: AskReference;
  option_id: string | null;
  user_input_answers?: UserInputAnswer[] | null;
};
export type EnvelopeKind = 'backend_event' | 'cli_write' | 'child_stopped' | 'answer_application';
export type ExecutorHealthRead = {
  admission_stopped: boolean;
  identity: ExecutorIdentity | null;
  last_contact_at_ms: number | null;
  oldest_item_age_ms: number | null;
  presence: PresenceClass;
  queue_depth: number | null;
  refusal: ExecutorRefusal | null;
  since_ms: number | null;
};
export type ExecutorIdentity = {
  application_revision: string;
  connection_generation?: number;
  executor_incarnation: string;
  protocol_version: number;
  spool_format_version: number;
};
export type ExecutorRefusal = {
  peer_identity: ExecutorIdentity | null;
  reason: string;
  refused_at_ms: number;
  server_identity: ExecutorIdentity;
};
export type FileManifest = {
  byte_count: number;
  owner_id: string;
  relative_path: string;
  scope: FileScope;
  sha256: string;
};
export type FileScope = 'ticket' | 'conversation';
export type FileSnapshot = { local_path: string; manifest: FileManifest };
export type FileUploadRequest = { child: ChildReference; manifest: FileManifest };
export type IpcOperation = 'read' | 'write' | 'retry' | 'health' | 'quiesce';
export type IpcRequest = {
  capability: string | null;
  child_id: string | null;
  operation: IpcOperation;
  payload: JsonObject;
  request_id: string;
  response_deadline?: number | null;
};
export type IpcResponse = {
  error: JsonObject | null;
  request_id: string;
  result: JsonObject | null;
  unconfirmed?: UnconfirmedCliResult | null;
};
export type IpcRetryPayload = { submission_id: string };
export type IpcWritePayload = { arguments: JsonObject; operation: string };
export type ItemBatch = { items: ItemSubmission[]; sender_id: string };
export type ItemBatchReply = { receipts: AcceptanceReceipt[]; stop?: ItemBatchStop | null };
export type ItemBatchStop = {
  message: string;
  missing_files?: FileManifest[];
  reason: 'retry_later' | 'missing_files';
  sequence: number;
};
export type ItemSubmission = { envelope: QueuedEnvelope; sender_refusal?: string | null };
export type JsonObject = { [key: string]: JsonValue };
export type JsonValue =
  boolean | number | string | JsonValue[] | { [key: string]: JsonValue } | null;
export type LeaseRefused = {
  error: JsonObject;
  refusal: ExecutorRefusal | null;
  update: MacUpdateCheck | null;
};
export type LeaseReply = {
  accepted_at_vps_ms: number;
  away_after_seconds: number;
  mailbox_high_sequence: number;
  update: MacUpdateCheck;
};
export type LeaseRequest = {
  admission_stopped: boolean;
  going_to_sleep?: boolean;
  identity: ExecutorIdentity;
  mac_wall_clock_ms: number;
  oldest_item_age_ms: number | null;
  process_id: number;
  queue_depth: number;
  waiting_conversations: WaitingConversation[];
  waiting_conversations_truncated: boolean;
  working_turns: WorkingTurn[];
};
export type LiveEventBatch = { events: BackendEvent[] };
export type LiveReadRequest = { child: ChildReference; params: JsonObject; path: string };
export type MacLocalStatus = {
  admission_stopped: boolean;
  going_to_sleep: boolean;
  identity: ExecutorIdentity;
  last_lease_failure: string | null;
  lease_accepted_at_mac_ms: number | null;
  process_id: number;
  refusal: ExecutorRefusal | null;
};
export type MacRuntimeManifest = {
  application_revision: string;
  architecture: string;
  platform: string;
  schema_version: number;
};
export type MacRuntimeSelection = {
  application_revision: string;
  archive_sha256: string;
  connection_generation: number;
  executor_incarnation: string;
  previous_revision: string;
  previous_runtime_path: string | null;
  request_id: string;
  runtime_path: string;
};
export type MacUpdateCheck = {
  prefetch?: MacUpdatePrefetch | null;
  schema_version: number;
  target: MacUpdateTarget | null;
};
export type MacUpdatePhase =
  'waiting' | 'downloading' | 'ready_to_restart' | 'current' | 'failed' | 'reverted';
export type MacUpdatePrefetch = {
  application_revision: string;
  archive_path: string;
  archive_sha256: string;
  archive_size: number;
};
export type MacUpdateReport = {
  application_revision: string;
  detail?: string | null;
  identity?: ExecutorIdentity | null;
  phase: MacUpdatePhase;
  request_id: string;
};
export type MacUpdateStatus = {
  detail?: string | null;
  phase: MacUpdatePhase | 'unavailable';
  request_id: string;
  target_revision: string;
};
export type MacUpdateTarget = {
  activate: boolean;
  application_revision: string;
  archive_path: string;
  archive_sha256: string;
  archive_size: number;
  mode: UpdateMode;
  request_id: string;
};
export type MailboxCommand = {
  child_command?: ChildCommand | null;
  command_id: string;
  command_sequence: number;
  created_at_vps_ms: number;
  model_list_request?: ModelListRequest | null;
};
export type MailboxPollReply = { commands: MailboxCommand[] };
export type MessageContent = MessagePiece[];
export type MessageFile = {
  byte_count: number;
  file_name: string;
  media_type: string;
  stored_file_id: string;
};
export type MessageImage = {
  file_name?: string | null;
  media_type: string;
  stored_file_id: string;
};
export type MessagePiece = MessageText | MessageImage | MessageFile;
export type MessageText = { text: string };
export type ModelListRequest = { backend_key: string; refresh: boolean };
export type ModelThinkingHappened = object;
export type PermissionAskOption = { label: string; option_id: string; option_kind: string };
export type PermissionAskRaised = { ask: BackendPermissionAsk };
export type PlanEntry = { status: PlanEntryStatus; text: string };
export type PlanEntryStatus = 'pending' | 'in_progress' | 'completed';
export type PlanUpdated = { entries: PlanEntry[] };
export type PresenceClass = 'present' | 'asleep' | 'not_heard_from' | 'never' | 'refused';
export type PromptDeliveryMode = 'queue' | 'send_now' | 'steer';
export type QueuedEnvelope = {
  conversation_id: string;
  file_dependencies?: FileManifest[];
  kind: EnvelopeKind;
  payload: JsonObject;
  sender_id: string;
  sequence: number;
  submission_id: string;
};
export type QuiesceRequest = { exit_when_ready: boolean };
export type Quiescence = {
  active_turn_count: number;
  admission_stopped: boolean;
  idle_children_stopped: boolean;
  pending_callback_count: number;
  uncertain_operation_count: number;
  unsent_entry_count: number;
};
export type QuiescenceReceipt = {
  exit_committed: boolean;
  identity: ExecutorIdentity;
  process_id: number;
  quiescence: Quiescence;
};
export type ReceiptDisposition = 'accepted' | 'refused' | 'uncertain';
export type RejectedCommandResult = { command_id: string; reason: string };
export type ResolvedConversationStart = {
  access: ConversationAccess;
  backend_key: ConversationBackendKey;
  conversation_id: string;
  model: string;
  reasoning_effort: string | null;
  role_materials: ConversationRoleMaterials | null;
  workspace_folder: string;
  workspace_path_policy?: WorkspacePathPolicy;
};
export type StartRun = {
  canonical_skill_snapshot: CanonicalSkillSnapshot;
  kind?: 'start';
  resolved_start: ResolvedConversationStart;
  vendor_session_cursor?: string | null;
  workspace_home_relative?: string | null;
};
export type Steer = {
  content: MessageContent;
  kind?: 'steer';
  sender_content?: MessageContent | null;
  sender_label: string;
};
export type StopRun = { kind?: 'stop' };
export type TokenUsageReported = {
  cached_input_tokens?: number | null;
  cost_usd?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
};
export type ToolCallFinished = {
  detail?: string | null;
  tool_call_id: string;
  tool_call_status: ToolCallStatus;
};
export type ToolCallProgress = { detail: string; tool_call_id: string };
export type ToolCallStarted = {
  detail?: string | null;
  title: string;
  tool_call_id: string;
  tool_kind: string;
};
export type ToolCallStatus = 'completed' | 'failed';
export type TurnEnded = {
  ending: ConversationTurnEnding;
  error_summary?: string | null;
  standard_error_tail?: string | null;
};
export type TurnToken = { conversation_id: string; turn_number: number };
export type UnconfirmedCliResult = {
  acceptance?: 'unconfirmed';
  publication: 'unknown' | 'preparing' | 'queued';
  request_id: string;
  submission_id: string;
};
export type UpdateMode = 'now' | 'idle';
export type UserInputAnswer = { answers: string[]; question_id: string };
export type UserInputFailed = { detail: string; request_id: string };
export type UserInputOption = { description: string; label: string };
export type UserInputQuestion = {
  allow_other: boolean;
  header: string;
  multi_select: boolean;
  options: UserInputOption[];
  question: string;
  question_id: string;
};
export type UserInputRequested = { request: BackendUserInputRequest };
export type VendorSessionCursorRebound = { vendor_session_cursor: string };
export type WaitingConversation = { conversation_id: string; oldest_sequence: number };
export type WorkingTurn = { child: ChildReference; tool_running: boolean; turn: TurnToken };
export type WorkspacePathPolicy = 'home_relative' | 'absolute' | 'executor_default';
export type WritePrompt = {
  automatic_compaction?: boolean;
  content: MessageContent;
  kind?: 'write_prompt';
  mode: PromptDeliveryMode;
  model_change?: string | null;
  reasoning_effort_change?: string | null;
  reasoning_effort_is_set?: boolean;
  sender_content: MessageContent;
  sender_label: string;
  sender_message_count?: number;
};
export interface WireTypes {
  AcceptanceReceipt: AcceptanceReceipt;
  AgentMessageCompleted: AgentMessageCompleted;
  AgentMessageDelta: AgentMessageDelta;
  AnswerApplication: AnswerApplication;
  AnswerPermission: AnswerPermission;
  AnswerUserInput: AnswerUserInput;
  AskReference: AskReference;
  BackendEvent: BackendEvent;
  BackendSnapshot: BackendSnapshot;
  BackendStreamEnded: BackendStreamEnded;
  BackgroundTaskEnded: BackgroundTaskEnded;
  BackgroundTaskStarted: BackgroundTaskStarted;
  CancelTurn: CancelTurn;
  CanonicalSkillSnapshot: CanonicalSkillSnapshot;
  CanonicalSkillSource: CanonicalSkillSource;
  ChildCommand: ChildCommand;
  ChildReference: ChildReference;
  CliWrite: CliWrite;
  CommandOutcome: CommandOutcome;
  CommandOutcomeReply: CommandOutcomeReply;
  CommandOutcomeReport: CommandOutcomeReport;
  CommandReplyReported: CommandReplyReported;
  CommandResult: CommandResult;
  ComposerCatalogReported: ComposerCatalogReported;
  ContextCompacted: ContextCompacted;
  DurableAnswer: DurableAnswer;
  ExecutorHealthRead: ExecutorHealthRead;
  ExecutorIdentity: ExecutorIdentity;
  ExecutorRefusal: ExecutorRefusal;
  FileManifest: FileManifest;
  FileSnapshot: FileSnapshot;
  FileUploadRequest: FileUploadRequest;
  IpcRequest: IpcRequest;
  IpcResponse: IpcResponse;
  IpcRetryPayload: IpcRetryPayload;
  IpcWritePayload: IpcWritePayload;
  ItemBatch: ItemBatch;
  ItemBatchReply: ItemBatchReply;
  ItemBatchStop: ItemBatchStop;
  ItemSubmission: ItemSubmission;
  JsonObject: JsonObject;
  LeaseRefused: LeaseRefused;
  LeaseReply: LeaseReply;
  LeaseRequest: LeaseRequest;
  LiveEventBatch: LiveEventBatch;
  LiveReadRequest: LiveReadRequest;
  MacLocalStatus: MacLocalStatus;
  MacRuntimeManifest: MacRuntimeManifest;
  MacRuntimeSelection: MacRuntimeSelection;
  MacUpdateCheck: MacUpdateCheck;
  MacUpdatePrefetch: MacUpdatePrefetch;
  MacUpdateReport: MacUpdateReport;
  MacUpdateStatus: MacUpdateStatus;
  MacUpdateTarget: MacUpdateTarget;
  MailboxCommand: MailboxCommand;
  MailboxPollReply: MailboxPollReply;
  ModelListRequest: ModelListRequest;
  ModelThinkingHappened: ModelThinkingHappened;
  PermissionAskRaised: PermissionAskRaised;
  PlanUpdated: PlanUpdated;
  QueuedEnvelope: QueuedEnvelope;
  QuiesceRequest: QuiesceRequest;
  Quiescence: Quiescence;
  QuiescenceReceipt: QuiescenceReceipt;
  RejectedCommandResult: RejectedCommandResult;
  ResolvedConversationStart: ResolvedConversationStart;
  StartRun: StartRun;
  Steer: Steer;
  StopRun: StopRun;
  TokenUsageReported: TokenUsageReported;
  ToolCallFinished: ToolCallFinished;
  ToolCallProgress: ToolCallProgress;
  ToolCallStarted: ToolCallStarted;
  TurnEnded: TurnEnded;
  TurnToken: TurnToken;
  UnconfirmedCliResult: UnconfirmedCliResult;
  UserInputAnswer: UserInputAnswer;
  UserInputFailed: UserInputFailed;
  UserInputRequested: UserInputRequested;
  VendorSessionCursorRebound: VendorSessionCursorRebound;
  WaitingConversation: WaitingConversation;
  WorkingTurn: WorkingTurn;
  WritePrompt: WritePrompt;
}
export type WireRootName = keyof WireTypes;
