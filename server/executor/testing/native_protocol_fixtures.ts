/** Native TS messages. No Python bytes, parser result, or schema sample supplies these values. */
import { fileURLToPath } from 'node:url';
import type { JsonObject, JsonValue, MessageContent, WireTypes } from '../wire.generated.ts';

function document(entries: readonly (readonly [string, JsonValue])[]): JsonObject {
  return Object.fromEntries(entries);
}
export function native_wire_fixtures(): WireTypes {
  const identity: WireTypes['ExecutorIdentity'] = {
    protocol_version: 22,
    application_revision: 'fixture',
    spool_format_version: 5,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
  };
  const child: WireTypes['ChildReference'] = {
    executor_incarnation: identity.executor_incarnation,
    child_id: '22222222-2222-4222-8222-222222222222',
    conversation_id: 'conversation',
  };
  const turn: WireTypes['TurnToken'] = { conversation_id: 'conversation', turn_number: 3 };
  const ask: WireTypes['AskReference'] = { child, turn, ask_id: 'ask', kind: 'permission' };
  const answer: WireTypes['DurableAnswer'] = {
    answer_id: 'answer',
    ask,
    option_id: 'allow',
    accepted_at_vps_ms: 100,
  };
  const answer_value: WireTypes['UserInputAnswer'] = {
    question_id: 'question',
    answers: ['One', 'Two'],
  };
  const user_answer: WireTypes['DurableAnswer'] = {
    ...answer,
    ask: { ...ask, kind: 'user_input', ask_id: 'request' },
    option_id: null,
    user_input_answers: [answer_value],
  };
  const manifest: WireTypes['FileManifest'] = {
    scope: 'ticket',
    owner_id: 'ticket',
    relative_path: 'artifact.txt',
    sha256: '0'.repeat(64),
    byte_count: 3,
  };
  const pieces: MessageContent = [
    { text: 'hello é' },
    { stored_file_id: 'image', media_type: 'image/png', file_name: 'image.png' },
    { stored_file_id: 'file', media_type: 'text/plain', file_name: 'artifact.txt', byte_count: 3 },
  ];
  const source = { skill_name: 'panels', source_text: 'Canonical α' };
  const skills = { sources: [source], required_skill_names: ['panels'] };
  const start: WireTypes['ResolvedConversationStart'] = {
    conversation_id: 'conversation',
    backend_key: 'codex',
    model: 'fixture',
    reasoning_effort: null,
    access: 'full',
    workspace_folder: '/tmp/unit22',
    role_materials: {
      role_text: 'Worker',
      identity_environment_variables: [['PLAN_ACTOR', 'ticket']],
    },
  };
  const prompt: WireTypes['WritePrompt'] = {
    content: pieces,
    sender_content: pieces,
    sender_label: 'Owner',
    mode: 'queue',
    kind: 'write_prompt',
    sender_message_count: 2,
    model_change: 'fixture-v2',
    reasoning_effort_change: 'low',
    reasoning_effort_is_set: true,
    automatic_compaction: true,
  };
  const command: WireTypes['ChildCommand'] = {
    command_id: 'prompt',
    child,
    operation: 'write_prompt',
    turn,
    arguments: prompt,
    file_dependencies: [manifest],
  };
  const backend_event: WireTypes['BackendEvent'] = {
    child,
    method: 'agent_message_delta',
    turn,
    arguments: { text_delta: 'news' },
  };
  const envelope: WireTypes['QueuedEnvelope'] = {
    sender_id: 'sender',
    conversation_id: 'conversation',
    sequence: 1,
    submission_id: 'submission',
    kind: 'backend_event',
    payload: backend_event,
    file_dependencies: [manifest],
  };
  const receipt: WireTypes['AcceptanceReceipt'] = {
    sender_id: 'sender',
    submission_id: 'submission',
    disposition: 'accepted',
    result: { recorded: true },
    accepted_at_vps_ms: 100,
  };
  const outcome: WireTypes['CommandOutcome'] = {
    command_id: 'prompt',
    child,
    disposition: 'succeeded',
    result: { kind: 'prompt_accepted', composed_content_delivered: true },
  };
  const snapshot: WireTypes['BackendSnapshot'] = {
    backend_key: 'codex',
    installed: true,
    executable_path: '/tmp/synthetic-codex',
    version: '1',
    identity: { status: 'authenticated', account_label: 'fixture' },
    available_models: [
      {
        model_id: 'fixture',
        display_name: 'Fixture',
        version: [1, 2],
        reasoning_effort_options: ['low'],
      },
    ],
    reasoning_effort_options: ['low'],
    default_model_id: 'fixture',
    default_reasoning_effort: 'low',
    update_advisory: {
      update_available: false,
      latest_version: '1',
      install_method: 'npm_global',
      update_command: ['fixture', 'update'],
      detail: 'fixture',
    },
    diagnoses: ['synthetic'],
    models_complete: true,
  };
  const model_request = { backend_key: 'codex', refresh: false };
  const mailbox: WireTypes['MailboxCommand'] = {
    command_sequence: 1,
    command_id: 'prompt',
    created_at_vps_ms: 100,
    child_command: command,
  };
  const rejected = { command_id: 'unknown', reason: 'unknown command' };
  const command_result = { command_id: 'prompt', child_outcome: outcome };
  const waiting = { conversation_id: 'conversation', oldest_sequence: 9 };
  const working = { child, turn, tool_running: true };
  const quiescence = {
    admission_stopped: true,
    active_turn_count: 1,
    pending_callback_count: 2,
    uncertain_operation_count: 0,
    unsent_entry_count: 1,
    idle_children_stopped: false,
  };
  const refusal: WireTypes['ExecutorRefusal'] = {
    reason: 'Mac needs update',
    peer_identity: null,
    server_identity: identity,
    refused_at_ms: 100,
  };
  const update_target: WireTypes['MacUpdateTarget'] = {
    request_id: 'update',
    application_revision: 'fixture',
    mode: 'now',
    activate: true,
    archive_sha256: '1'.repeat(64),
    archive_size: 3,
    archive_path: '/api/executor/update/update/archive',
  };
  const prefetch = {
    application_revision: 'fixture',
    archive_sha256: '1'.repeat(64),
    archive_size: 3,
    archive_path: '/api/executor/update/prefetch/' + '1'.repeat(64),
  };
  const update_check = { schema_version: 1, target: update_target, prefetch };
  // Opaque documents contain ordinary JSON values.
  const result = document([
    ['2', 1],
    ['1', 1e-7],
    ['recorded', true],
    ['counts', [1, 1, 1.5, 1234, '1', 0]],
  ]);
  return {
    ExecutorIdentity: identity,
    ChildReference: child,
    TurnToken: turn,
    AskReference: ask,
    DurableAnswer: user_answer,
    AnswerApplication: { answer_id: 'answer', ask, applied_at_mac_ms: 110 },
    CanonicalSkillSource: source,
    CanonicalSkillSnapshot: skills,
    FileManifest: manifest,
    FileSnapshot: { manifest, local_path: '/tmp/unit22/artifact.txt' },
    FileUploadRequest: { manifest, child },
    ResolvedConversationStart: start,
    StartRun: {
      resolved_start: start,
      canonical_skill_snapshot: skills,
      kind: 'start',
      vendor_session_cursor: 'cursor',
      workspace_home_relative: 'unit22',
    },
    WritePrompt: prompt,
    Steer: { content: pieces, sender_content: pieces, sender_label: 'Owner', kind: 'steer' },
    CancelTurn: { kind: 'cancel_running_turn' },
    StopRun: { kind: 'stop' },
    AnswerPermission: { ask_id: 'ask', option_id: 'allow', answer, kind: 'answer_permission_ask' },
    AnswerUserInput: {
      request_id: 'request',
      answers: [answer_value],
      answer: user_answer,
      kind: 'answer_user_input',
    },
    ChildCommand: command,
    MailboxCommand: mailbox,
    MailboxPollReply: {
      commands: [
        mailbox,
        {
          command_sequence: 2,
          command_id: 'models',
          created_at_vps_ms: 101,
          model_list_request: model_request,
        },
      ],
    },
    ModelListRequest: model_request,
    CommandOutcome: outcome,
    CommandResult: command_result,
    CommandOutcomeReport: {
      outcomes: [command_result, { command_id: 'models', model_list: snapshot }],
    },
    RejectedCommandResult: rejected,
    CommandOutcomeReply: { recorded: ['prompt'], rejected: [rejected] },
    CliWrite: {
      operation: 'worker.propose',
      arguments: { text: 'fixture', optional: null },
      child,
      turn,
    },
    QueuedEnvelope: envelope,
    AcceptanceReceipt: receipt,
    ItemSubmission: { envelope, sender_refusal: 'fixture preparation failed' },
    ItemBatch: {
      sender_id: 'sender',
      items: [{ envelope, sender_refusal: 'fixture preparation failed' }],
    },
    ItemBatchStop: {
      sequence: 2,
      reason: 'missing_files',
      message: 'upload first',
      missing_files: [manifest],
    },
    ItemBatchReply: {
      receipts: [receipt],
      stop: { sequence: 2, reason: 'retry_later', message: 'retry' },
    },
    BackendEvent: backend_event,
    LiveEventBatch: { events: [backend_event] },
    LiveReadRequest: { child, path: '/api/tickets', params: { limit: 2 } },
    WaitingConversation: waiting,
    WorkingTurn: working,
    LeaseRequest: {
      identity,
      process_id: 42,
      mac_wall_clock_ms: 100,
      queue_depth: 2,
      oldest_item_age_ms: 5,
      waiting_conversations: [waiting],
      waiting_conversations_truncated: false,
      working_turns: [working],
      admission_stopped: false,
      going_to_sleep: true,
    },
    LeaseReply: {
      accepted_at_vps_ms: 100,
      away_after_seconds: 60,
      update: update_check,
      mailbox_high_sequence: 2,
    },
    ExecutorRefusal: refusal,
    LeaseRefused: {
      error: { code: 'executor_version_refused', message: 'update', detail: { reason: 'fixture' } },
      refusal: { ...refusal, peer_identity: identity },
      update: update_check,
    },
    ExecutorHealthRead: {
      presence: 'present',
      since_ms: 100,
      identity,
      last_contact_at_ms: 100,
      refusal,
      queue_depth: 2,
      oldest_item_age_ms: 5,
      admission_stopped: false,
    },
    IpcRequest: {
      request_id: 'request',
      operation: 'read',
      child_id: 'child',
      capability: 'synthetic',
      payload: { path: '/api/tickets', params: {} },
      response_deadline: 12.5,
    },
    IpcResponse: {
      request_id: 'request',
      result,
      error: { code: 'fixture', message: 'fixture failure', detail: { retry: true } },
      unconfirmed: { request_id: 'request', submission_id: 'submission', publication: 'queued' },
    },
    IpcRetryPayload: { submission_id: 'submission' },
    IpcWritePayload: { operation: 'worker.propose', arguments: { text: 'fixture' } },
    UnconfirmedCliResult: {
      request_id: 'request',
      submission_id: 'submission',
      publication: 'queued',
    },
    QuiesceRequest: { exit_when_ready: true },
    Quiescence: quiescence,
    QuiescenceReceipt: { identity, process_id: 42, quiescence, exit_committed: false },
    MacLocalStatus: {
      identity,
      process_id: 42,
      lease_accepted_at_mac_ms: 100,
      last_lease_failure: 'synthetic',
      refusal,
      admission_stopped: false,
      going_to_sleep: true,
    },
    MacRuntimeManifest: {
      schema_version: 1,
      application_revision: 'fixture',
      platform: 'darwin',
      architecture: 'arm64',
    },
    MacRuntimeSelection: {
      request_id: 'update',
      application_revision: 'fixture',
      runtime_path: '/tmp/unit22/runtime',
      previous_runtime_path: null,
      previous_revision: 'old',
      executor_incarnation: identity.executor_incarnation,
      connection_generation: 0,
      archive_sha256: '1'.repeat(64),
    },
    MacUpdateCheck: update_check,
    MacUpdatePrefetch: prefetch,
    MacUpdateReport: {
      request_id: 'update',
      application_revision: 'fixture',
      phase: 'current',
      identity,
    },
    MacUpdateStatus: { request_id: 'update', target_revision: 'fixture', phase: 'waiting' },
    MacUpdateTarget: update_target,
    BackendSnapshot: snapshot,
    UserInputAnswer: answer_value,
    JsonObject: {
      path: '/api/tickets',
      data: [{ ready: true }, null, 1, 1234],
    },
    AgentMessageDelta: { text_delta: 'news' },
    ModelThinkingHappened: {},
    AgentMessageCompleted: { content: pieces },
    CommandReplyReported: { reply_text: 'done' },
    ToolCallStarted: { tool_call_id: 'tool', title: 'Read', tool_kind: 'read' },
    ToolCallProgress: { tool_call_id: 'tool', detail: 'progress' },
    ToolCallFinished: { tool_call_id: 'tool', tool_call_status: 'completed' },
    PlanUpdated: { entries: [{ text: 'Check wire', status: 'in_progress' }] },
    TokenUsageReported: { input_tokens: 10, output_tokens: 2, cached_input_tokens: 3, cost_usd: 2 },
    ContextCompacted: {},
    PermissionAskRaised: {
      ask: {
        ask_id: 'ask',
        title: 'Allow?',
        detail: null,
        options: [{ option_id: 'allow', label: 'Allow', option_kind: 'allow_once' }],
      },
    },
    UserInputRequested: {
      request: {
        request_id: 'request',
        questions: [
          {
            question_id: 'question',
            header: 'Choice',
            question: 'Choose',
            options: [{ label: 'One', description: 'First' }],
            multi_select: true,
            allow_other: false,
          },
        ],
      },
    },
    UserInputFailed: { request_id: 'request', detail: 'expired' },
    TurnEnded: {
      ending: 'failed',
      error_summary: 'fixture summary',
      standard_error_tail: 'fixture stderr',
    },
    VendorSessionCursorRebound: { vendor_session_cursor: 'cursor' },
    ComposerCatalogReported: {
      composer_catalog: [
        { kind: 'command', display_text: 'Help', insertion_text: '/help', description: 'Help' },
      ],
    },
    BackgroundTaskStarted: { task_id: 'task', task_description: 'Work' },
    BackgroundTaskEnded: { task_id: 'task', task_status: 'completed' },
    BackendStreamEnded: {},
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 3 || process.argv[2] !== '--export')
    throw new Error('Usage: native_protocol_fixtures.ts --export');
  process.stdout.write(JSON.stringify(native_wire_fixtures()));
}
