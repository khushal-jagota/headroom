import { readFileSync } from 'node:fs';
import { parse_backend_instance_id } from '../../core/identity.ts';
/** A terminal reader failure must not prevent cleanup of its exact injected client. */
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { expect, it, vi } from 'vitest';
import { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import {
  AgentMessageCompleted,
  BackendStreamEnded,
  BackendSpawnFailed,
  BackendInitializationTimeout,
  NeedsRebind,
  PromptWriteFailed,
  SessionLoadFailed,
  TokenUsageReported,
  TurnEnded,
  ToolCallStarted,
} from '../../../conversation/backends/contracts.ts';
import { text_message_content } from '../../../conversation/message_content.ts';
import { ClaudeAgentSdkBackendChild } from './adapter.ts';
import type { ClaudeSdkClient, ClaudeSdkMessage, ClaudeSdkOptions } from './query_contracts.ts';
import { Deferred } from '../../../core/deferred.ts';
import { uncontained_worker_supervision } from '../../testing/uncontained_supervision.ts';
import { CLAUDE_CANCEL_SETTLEMENT_TIMEOUT_SECONDS } from './configuration.ts';

it('clears a failed reader and disconnects once across repeated stop calls', async () => {
  const next = new Deferred<IteratorResult<ClaudeSdkMessage>>();
  const failedReport = new Deferred<void>();
  const failure = Object.assign(new Error('database is locked'), { code: 'SQLITE_BUSY' });
  const disconnect = vi.fn(async (_options?: { deadline: number }) => {});
  const native_settled = new Deferred<void>();
  const client: ClaudeSdkClient = {
    connect: async () => {},
    get_server_info: async () => null,
    query: async () => {},
    receive_messages: () => ({ [Symbol.asyncIterator]: () => ({ next: () => next.promise }) }),
    watch_user_message: () => {},
    forget_user_message: () => {},
    wait_for_user_message_admission: async () => null,
    user_message_uuids_for_result: () => new Set(),
    user_message_is_settled: () => true,
    wait_for_user_message_settlement: async () => {},
    interrupt_and_cancel_queued: async () => [new Set(), new Set()],
    apply_reasoning_effort: async () => {},
    disconnect,
    settled: () => native_settled.promise,
  };
  const start = new ResolvedConversationStart({
    conversation_id: 'failed-reader',
    backend_key: parse_backend_instance_id('claude'),
    model: 'haiku',
    reasoning_effort: 'low',
    role_materials: null,
    workspace_folder: '/synthetic/workspace',
    workspace_path_policy: 'absolute',
    access: 'full',
  });
  const child_processes = new ChildProcesses();
  const one_shot_children = new OneShotChildren(child_processes);
  const child = new ClaudeAgentSdkBackendChild({
    child_processes,
    worker_supervision: uncontained_worker_supervision(one_shot_children),
    one_shot_children,
    launch: { claude_executable: '/synthetic/claude' },
    resolved_start: start,
    client_factory: () => client,
    event_sink: {
      report: async (_turn, event) => {
        if (event instanceof BackendStreamEnded) {
          failedReport.resolve();
          throw failure;
        }
      },
    },
    message_files: {
      keep: async () => {
        throw new Error('No message file belongs to this check');
      },
      read: async () => new Uint8Array(),
      media_type_of: async () => null,
      path_of: () => '/synthetic/unused',
    },
  });
  const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
  try {
    await child.start(start, { vendor_session_cursor: null });
    next.resolve({ value: undefined, done: true });
    await failedReport.promise;
    const options = { deadline: performance.now() / 1000 + 2 };
    await child.detach(options);
    await child.stop();
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalledWith(options);
    let actually_settled = false;
    const joined = child.settled().then(() => {
      actually_settled = true;
    });
    await Promise.resolve();
    expect(actually_settled).toBe(false);
    native_settled.resolve();
    await joined;
    expect(logged).toHaveBeenCalledExactlyOnceWith(
      'conversation failed-reader: Claude message reader failed during cleanup',
      failure,
    );
  } finally {
    native_settled.resolve();
    logged.mockRestore();
  }
});

// A native initialization timeout precedes prompt delivery, even on resume. Other
// connect failures retain their ordinary spawn/session classification.
it('classifies only the native SDK initialization-timeout discriminator before resume failures', async () => {
  const start = new ResolvedConversationStart({
    conversation_id: 'initialize-retry',
    backend_key: parse_backend_instance_id('claude'),
    model: 'haiku',
    reasoning_effort: null,
    role_materials: null,
    workspace_folder: '/synthetic/workspace',
    workspace_path_policy: 'absolute',
    access: 'full',
  });
  const unexpected = async (): Promise<never> => {
    throw new Error('No prompt or stream before initialization');
  };
  for (const cursor of [null, 'retained-session']) {
    for (const tagged of [false, true]) {
      const error = new Error('Native initialization did not complete');
      if (tagged) Object.assign(error, { errorClass: 'initialize_timeout' });
      const disconnect = vi.fn(async () => {});
      const client: ClaudeSdkClient = {
        connect: async () => {
          throw error;
        },
        get_server_info: unexpected,
        query: unexpected,
        receive_messages: () => {
          throw new Error('No stream before initialization');
        },
        watch_user_message() {},
        forget_user_message() {},
        wait_for_user_message_admission: unexpected,
        user_message_uuids_for_result: () => new Set(),
        user_message_is_settled: () => true,
        wait_for_user_message_settlement: unexpected,
        interrupt_and_cancel_queued: unexpected,
        apply_reasoning_effort: unexpected,
        disconnect,
        settled: async () => {},
      };
      const child_processes = new ChildProcesses();
      const one_shot_children = new OneShotChildren(child_processes);
      const child = new ClaudeAgentSdkBackendChild({
        child_processes,
        one_shot_children,
        worker_supervision: uncontained_worker_supervision(one_shot_children),
        launch: { claude_executable: '/synthetic/claude' },
        resolved_start: start,
        client_factory: () => client,
        event_sink: { report: unexpected },
        message_files: {
          keep: unexpected,
          read: unexpected,
          media_type_of: async () => null,
          path_of: () => '/synthetic/unused',
        },
      });
      await expect(child.start(start, { vendor_session_cursor: cursor })).rejects.toBeInstanceOf(
        tagged
          ? BackendInitializationTimeout
          : cursor === null
            ? BackendSpawnFailed
            : SessionLoadFailed,
      );
      await child.stop();
      expect(disconnect).toHaveBeenCalledOnce();
    }
  }
});

/** A started child on a client that admits every prompt and yields what the test delivers. */
async function live_child(
  conversation_id: string,
  options: {
    vendor_session_cursor?: string | null;
    client?: Partial<ClaudeSdkClient>;
    start_agent_turn?: import('../../../conversation/backends/contracts.ts').BackendEventSink['start_agent_turn'];
    message_files?: import('../../../conversation/message_files.ts').ConversationMessageFiles;
  } = {},
) {
  const delivered: IteratorResult<ClaudeSdkMessage>[] = [];
  let arrived = new Deferred<void>();
  const deliver = (result: IteratorResult<ClaudeSdkMessage>) => {
    delivered.push(result);
    arrived.resolve();
  };
  const client: ClaudeSdkClient = {
    connect: async () => {},
    get_server_info: async () => null,
    query: async () => {},
    receive_messages: () => ({
      [Symbol.asyncIterator]: () => ({
        next: async () => {
          while (!delivered.length) {
            arrived = new Deferred<void>();
            await arrived.promise;
          }
          return delivered.shift()!;
        },
      }),
    }),
    watch_user_message: () => {},
    forget_user_message: () => {},
    wait_for_user_message_admission: async () => true,
    user_message_uuids_for_result: () => new Set(),
    user_message_is_settled: () => true,
    wait_for_user_message_settlement: async () => {},
    interrupt_and_cancel_queued: async () => [new Set(), new Set()],
    apply_reasoning_effort: async () => {},
    disconnect: async () => {},
    ...options.client,
  };
  const start = new ResolvedConversationStart({
    conversation_id,
    backend_key: parse_backend_instance_id('claude'),
    model: 'haiku',
    reasoning_effort: 'low',
    role_materials: null,
    workspace_folder: '/synthetic/workspace',
    workspace_path_policy: 'absolute',
    access: 'full',
  });
  const turn_endings: TurnEnded[] = [];
  const reported: unknown[] = [];
  const stream_ended = new Deferred<void>();
  const child_processes = new ChildProcesses();
  const one_shot_children = new OneShotChildren(child_processes);
  let sdk_options!: ClaudeSdkOptions;
  const child = new ClaudeAgentSdkBackendChild({
    child_processes,
    one_shot_children,
    worker_supervision: uncontained_worker_supervision(one_shot_children),
    launch: { claude_executable: '/synthetic/claude' },
    resolved_start: start,
    client_factory: (value) => {
      sdk_options = value;
      return client;
    },
    event_sink: {
      start_agent_turn: options.start_agent_turn,
      report: async (_turn, event) => {
        reported.push(event);
        if (event instanceof TurnEnded) turn_endings.push(event);
        if (event instanceof BackendStreamEnded) stream_ended.resolve();
      },
    },
    message_files: options.message_files ?? {
      keep: async () => {
        throw new Error('No message file belongs to this check');
      },
      read: async () => new Uint8Array(),
      media_type_of: async () => null,
      path_of: () => '/synthetic/unused',
    },
  });
  await child.start(start, { vendor_session_cursor: options.vendor_session_cursor ?? null });
  const content = text_message_content('hello');
  const prompt = (turn_number: number, reasoning_effort_change: string | null) =>
    child.write_prompt({ conversation_id, turn_number }, content, {
      sender_label: 'Owner',
      sender_content: content,
      mode: 'send_now',
      model_change: null,
      reasoning_effort_change,
      reasoning_effort_is_set: true,
    });
  return {
    child,
    can_use_tool: sdk_options.canUseTool!,
    content,
    prompt,
    deliver,
    turn_endings,
    reported,
    stream_ended: stream_ended.promise,
  };
}

// Risk: a caller that writes without the core's selection check (the Mac runner, a replayed
// prompt) runs a turn on a model or effort the session never took.
it('applies a live effort change and refuses a change only a fresh child can carry', async () => {
  const apply_reasoning_effort = vi.fn(async (_level: string) => {});
  const { child, content, prompt } = await live_child('live-effort', {
    client: { apply_reasoning_effort },
  });
  await prompt(1, 'high');
  expect(apply_reasoning_effort).toHaveBeenCalledExactlyOnceWith('high');
  await prompt(2, 'high');
  expect(apply_reasoning_effort).toHaveBeenCalledTimes(1);
  await expect(prompt(3, null)).rejects.toBeInstanceOf(NeedsRebind);
  await expect(
    child.write_prompt({ conversation_id: 'live-effort', turn_number: 3 }, content, {
      sender_label: 'Owner',
      sender_content: content,
      mode: 'send_now',
      model_change: 'opus',
      reasoning_effort_change: null,
    }),
  ).rejects.toBeInstanceOf(NeedsRebind);
  expect(apply_reasoning_effort).toHaveBeenCalledTimes(1);
  apply_reasoning_effort.mockRejectedValueOnce(new Error('control channel closed'));
  await expect(prompt(4, 'max')).rejects.toBeInstanceOf(PromptWriteFailed);
  // The failed apply broke the wire, so even an unchanged prompt needs a new child.
  await expect(prompt(5, 'high')).rejects.toMatchObject({ failed_child_recovery: true });
  await child.stop();
});

it('ends the turn broken when Claude answers under a session other than the one resumed', async () => {
  const { child, prompt, deliver, turn_endings } = await live_child('session-mismatch', {
    vendor_session_cursor: 'retained-session',
  });
  await prompt(1, 'low');
  deliver({
    value: {
      type: 'stream_event',
      uuid: 'event',
      session_id: 'substitute-session',
      event: { type: 'message_start' },
    },
    done: false,
  });
  await vi.waitFor(() => expect(turn_endings).toHaveLength(1));
  expect(turn_endings[0]).toMatchObject({ ending: 'failed', child_disposition: 'broken' });
  await child.stop();
});

it('ends the turn broken after a cancel that Claude never settles, ignoring its late result', async () => {
  const { child, prompt, deliver, turn_endings, stream_ended } =
    await live_child('cancel-unsettled');
  await prompt(1, 'low');
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    const cancelled = child.cancel_running_turn();
    const refused = expect(cancelled).rejects.toBeInstanceOf(PromptWriteFailed);
    await vi.advanceTimersByTimeAsync(CLAUDE_CANCEL_SETTLEMENT_TIMEOUT_SECONDS * 1000);
    await refused;
  } finally {
    vi.useRealTimers();
  }
  deliver({
    value: {
      type: 'result',
      subtype: 'success',
      duration_ms: 1,
      duration_api_ms: 1,
      is_error: false,
      num_turns: 1,
      session_id: null,
      terminal_reason: 'aborted_streaming',
    },
    done: false,
  });
  deliver({ value: undefined, done: true });
  await stream_ended;
  expect(turn_endings).toHaveLength(1);
  expect(turn_endings[0]).toMatchObject({ ending: 'failed', child_disposition: 'broken' });
  await child.stop();
});

/** A started child whose results name the prompts that the test says they name. */
async function child_with_named_results(
  conversation_id: string,
  client: Partial<ClaudeSdkClient> = {},
) {
  const prompt_uuids: string[] = [];
  const named_by_result = new Map<string, ReadonlySet<string>>();
  const live = await live_child(conversation_id, {
    client: {
      ...client,
      watch_user_message: (uuid) => void prompt_uuids.push(uuid),
      user_message_uuids_for_result: (uuid) => named_by_result.get(uuid ?? '') ?? new Set(),
    },
  });
  const result = (uuid: string, names: string[], fields: Record<string, unknown> = {}) => {
    named_by_result.set(uuid, new Set(names));
    live.deliver({
      value: {
        type: 'result',
        subtype: 'success',
        uuid,
        duration_ms: 1,
        duration_api_ms: 1,
        num_turns: 1,
        is_error: false,
        session_id: null,
        usage: { input_tokens: 0, output_tokens: 0 },
        ...fields,
      },
      done: false,
    });
  };
  return { ...live, prompt_uuids, result };
}

// Risk: after a restart killed background tasks, the resumed CLI answers its own task notice
// with an empty result after the Panels prompt is queued. That result ended the Panels turn,
// and the real answer then ran with no turn open, so Panels dropped it.
it('ends a resumed turn on the result for its prompt, not on the CLI result before it', async () => {
  const { child, prompt, deliver, turn_endings, reported, prompt_uuids, result } =
    await child_with_named_results('resume-own-turn');
  await prompt(1, 'low');
  const [prompt_uuid] = prompt_uuids;
  const frame = (value: ClaudeSdkMessage) => deliver({ value, done: false });
  frame({
    type: 'system',
    subtype: 'task_notification',
    task_id: 'killed',
    status: 'stopped',
    output_file: '/synthetic/killed.output',
    summary: 'Background command stopped',
    uuid: 'notice',
    session_id: null,
  });
  frame({ type: 'command_lifecycle', command_uuid: prompt_uuid, state: 'queued' });
  result('own-result', [], { usage: { input_tokens: 0, output_tokens: 0 }, num_turns: 0 });
  frame({ type: 'command_lifecycle', command_uuid: prompt_uuid, state: 'started' });
  frame({
    type: 'assistant',
    uuid: 'answer',
    session_id: null,
    parent_tool_use_id: null,
    message: { role: 'assistant', model: 'haiku', content: [{ type: 'text', text: 'Done.' }] },
  });
  result('prompt-result', [prompt_uuid!], { usage: { input_tokens: 7, output_tokens: 3 } });
  await vi.waitFor(() => expect(turn_endings).toHaveLength(1));
  expect(turn_endings[0]).toMatchObject({ ending: 'completed', error_summary: null });
  const relevant = reported.filter(
    (event) =>
      event instanceof AgentMessageCompleted ||
      event instanceof TokenUsageReported ||
      event instanceof TurnEnded,
  );
  expect(relevant.map((event) => (event as object).constructor)).toEqual([
    AgentMessageCompleted,
    TokenUsageReported,
    TurnEnded,
  ]);
  expect(relevant[1]).toMatchObject({ input_tokens: 7, output_tokens: 3 });
  await child.stop();
});

// Risk: an error result that names no prompt fails the turn while its prompt still runs. A real
// session failure must still fail the turn, through the end of the message stream.
it('keeps a turn open across an error result for no prompt, and fails it when the stream ends', async () => {
  const { child, prompt, deliver, turn_endings, reported, result, stream_ended } =
    await child_with_named_results('error-own-turn');
  await prompt(1, 'low');
  result('own-error', [], { subtype: 'error_during_execution', is_error: true });
  deliver({ value: undefined, done: true });
  await stream_ended;
  expect(turn_endings).toHaveLength(1);
  expect(turn_endings[0]).toMatchObject({ ending: 'failed', child_disposition: 'broken' });
  expect(turn_endings[0]!.error_summary).toContain("the backend's message stream ended");
  expect(reported.some((event) => event instanceof TokenUsageReported)).toBe(false);
  await child.stop();
});

// Risk: a turn whose prompt has no id, or a cancelled turn, stops ending on a result that names no
// prompt. A steer into a compaction turn, or a cancel, would then leave the turn open.
it('ends a turn without a prompt id, and a cancelled turn, on a result that names no prompt', async () => {
  const { child, content, prompt, turn_endings, result } =
    await child_with_named_results('old-endings');
  const token = { conversation_id: 'old-endings', turn_number: 1 };
  await child.write_prompt(token, content, {
    sender_label: 'Owner',
    sender_content: content,
    mode: 'send_now',
    model_change: null,
    reasoning_effort_change: null,
    automatic_compaction: true,
  });
  await child.steer(token, content, { sender_label: 'Owner' });
  result('compaction-result', []);
  await vi.waitFor(() => expect(turn_endings).toHaveLength(1));
  expect(turn_endings[0]).toMatchObject({ ending: 'completed' });
  await prompt(2, 'low');
  const cancelled = child.cancel_running_turn();
  result('interrupted-result', [], { terminal_reason: 'aborted_streaming' });
  await cancelled;
  expect(turn_endings[1]).toMatchObject({ ending: 'interrupted', child_disposition: 'reusable' });
  await child.stop();
});

// bypassPermissions skips the permission modes but not Claude Code's dangerous-rm check, which
// then asks a person. Full access must start in auto mode.
it('starts a full-access conversation in auto permission mode', async () => {
  const start = new ResolvedConversationStart({
    conversation_id: 'permission-mode',
    backend_key: parse_backend_instance_id('claude'),
    model: 'haiku',
    reasoning_effort: null,
    role_materials: null,
    workspace_folder: '/synthetic/workspace',
    workspace_path_policy: 'absolute',
    access: 'full',
  });
  const client_factory = vi.fn((_options: unknown) => {
    throw new Error('stop after options');
  });
  const child_processes = new ChildProcesses();
  const one_shot_children = new OneShotChildren(child_processes);
  const child = new ClaudeAgentSdkBackendChild({
    child_processes,
    one_shot_children,
    worker_supervision: uncontained_worker_supervision(one_shot_children),
    launch: { claude_executable: '/synthetic/claude' },
    resolved_start: start,
    client_factory: client_factory as never,
    event_sink: { report: async () => {} },
    message_files: {
      keep: async () => new Uint8Array() as never,
      read: async () => new Uint8Array(),
      media_type_of: async () => null,
      path_of: () => '/synthetic/unused',
    },
  });
  await child.start(start, { vendor_session_cursor: null }).catch(() => {});
  expect(client_factory).toHaveBeenCalledTimes(1);
  const options = client_factory.mock.calls[0]![0] as Record<string, unknown>;
  expect(options.permissionMode).toBe('auto');
  expect(options).not.toHaveProperty('allowDangerouslySkipPermissions');
});

// Risk: a root task hand-back after the prompted turn ends previously lost every event.
// Recorded through the real driver capture tee with Claude 2.1.322 / Haiku on 2026-11-24.
// The nested Bash task also finishes first. It must not start a parent turn.
it('replays a recorded background hand-back as a new turn with tools, text, and usage', async () => {
  const frames = JSON.parse(
    readFileSync(
      new URL('../testing/fixtures/background-handback.recorded.json', import.meta.url),
      'utf8',
    ),
  ) as ClaudeSdkMessage[];
  const names = new Map(
    frames
      .filter((frame) => frame.type === 'result')
      .map((frame) => [String(frame.uuid), frame.user_message_uuids as string[]]),
  );
  let prompt_uuid = '';
  const start_agent_turn = vi.fn(async () => ({
    conversation_id: 'background-handback',
    turn_number: 2,
  }));
  const { child, prompt, deliver, reported, turn_endings } = await live_child(
    'background-handback',
    {
      start_agent_turn,
      client: {
        watch_user_message: (uuid) => {
          prompt_uuid = uuid;
        },
        user_message_uuids_for_result: (uuid) =>
          new Set(
            (names.get(uuid ?? '') ?? []).map((name) => (name === '<prompt>' ? prompt_uuid : name)),
          ),
      },
    },
  );
  try {
    await prompt(1, 'low');
    for (const frame of frames) deliver({ value: frame, done: false });
    await vi.waitFor(() => expect(turn_endings).toHaveLength(2));
    expect(start_agent_turn).toHaveBeenCalledExactlyOnceWith(
      'Background task "Background sleep and report" completed',
      expect.any(AbortSignal),
    );
    expect(turn_endings.map((event) => event.ending)).toEqual(['completed', 'completed']);
    const after_first_end = reported.slice(reported.indexOf(turn_endings[0]!) + 1);
    expect(
      after_first_end.some((event) => event instanceof ToolCallStarted && event.title === 'Bash'),
    ).toBe(true);
    expect(after_first_end.some((event) => event instanceof AgentMessageCompleted)).toBe(true);
    expect(after_first_end.some((event) => event instanceof TokenUsageReported)).toBe(true);
  } finally {
    await child.stop();
  }
});

// Gap: AskUserQuestion answer callbacks never carried a managed file reference to readable bytes.
it('Claude receives a readable managed image reference with its structured answer', async () => {
  const { mkdtempSync, readFileSync, rmSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const { ConversationMessageFiles } =
    await import('../../../conversation/record/message_files.ts');
  const { MessageImage } = await import('../../../conversation/message_content.ts');
  const { UserInputAnswer } = await import('../../../conversation/events.ts');
  const { UserInputRequested } = await import('../../../conversation/backends/contracts.ts');
  const root = mkdtempSync(join(tmpdir(), 'claude-answer-'));
  const files = new ConversationMessageFiles(join(root, 'record.sqlite'));
  const state = await live_child('answer-image', { message_files: files });
  try {
    await state.prompt(1, 'low');
    const response = state.can_use_tool(
      'AskUserQuestion',
      {
        questions: [
          {
            question: 'What proof?',
            header: 'Proof',
            options: [{ label: 'Screenshot', description: 'A picture' }],
            multiSelect: false,
          },
        ],
      },
      { signal: new AbortController().signal, toolUseID: 'question', requestId: 'question' },
    );
    await vi.waitFor(() =>
      expect(state.reported.some((event) => event instanceof UserInputRequested)).toBe(true),
    );
    const request = state.reported.find(
      (event) => event instanceof UserInputRequested,
    ) as InstanceType<typeof UserInputRequested>;
    const bytes = Buffer.from('PNG fixture bytes');
    const kept = await files.keep('answer-image', bytes, { media_type: 'image/png' });
    await state.child.answer_user_input(request.request.request_id, [
      new UserInputAnswer({
        question_id: 'What proof?',
        answers: ['Use this screenshot'],
        attachments: [
          new MessageImage({
            stored_file_id: kept.stored_file_id,
            file_name: 'proof.png',
            media_type: 'image/png',
          }),
        ],
      }),
    ]);
    const native = await response;
    if (native === null || native.behavior !== 'allow') throw new Error('Question was not allowed');
    const answer = (native.updatedInput?.answers as Record<string, string>)['What proof?'];
    expect(answer).toContain('Use this screenshot');
    const reference = answer.match(/is available at (.+)\.$/)![1];
    expect(readFileSync(reference)).toEqual(bytes);
  } finally {
    await state.child.stop();
    rmSync(root, { recursive: true, force: true });
  }
});

// Gap: cancellation arrived after the home accepted a turn but before its receipt reached
// the driver. Reuse the existing client and recorded task frames, with no connected host.
it('waits for autonomous activation before cancellation and then accepts the next prompt', async () => {
  const frames = JSON.parse(
    readFileSync(
      new URL('../testing/fixtures/background-handback.recorded.json', import.meta.url),
      'utf8',
    ),
  ) as ClaudeSdkMessage[];
  const notice = frames.findLast((frame) => frame.subtype === 'task_notification')!;
  const snapshot = frames.find((frame) => frame.subtype === 'background_tasks_changed')!;
  const gate = new Deferred<
    import('../../../conversation/backends/contracts.ts').TurnToken | null
  >();
  const start_agent_turn = vi.fn(() => gate.promise);
  const interrupt = vi.fn(async () => [new Set<string>(), new Set<string>()] as const);
  const state = await live_child('pending-cancel', {
    start_agent_turn,
    client: { interrupt_and_cancel_queued: interrupt },
  });
  try {
    state.deliver({ value: snapshot, done: false });
    state.deliver({ value: notice, done: false });
    await vi.waitFor(() => expect(start_agent_turn).toHaveBeenCalledOnce());
    let cancelled = false;
    const cancellation = state.child.cancel_running_turn().then(() => {
      cancelled = true;
    });
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(cancelled).toBe(false);
    expect(interrupt).not.toHaveBeenCalled();
    gate.resolve({ conversation_id: 'pending-cancel', turn_number: 2 });
    await vi.waitFor(() => expect(interrupt).toHaveBeenCalledOnce());
    const result = frames.findLast((frame) => frame.type === 'result')!;
    state.deliver({ value: result, done: false });
    await cancellation;
    expect(state.turn_endings).toMatchObject([{ ending: 'interrupted' }]);
    await state.prompt(3, 'low');
  } finally {
    gate.resolve(null);
    await state.child.stop();
  }
});

// Gap: an unavailable home must not keep the SDK reader alive during native shutdown.
it('aborts a pending autonomous receipt when the Claude child stops', async () => {
  const frames = JSON.parse(
    readFileSync(
      new URL('../testing/fixtures/background-handback.recorded.json', import.meta.url),
      'utf8',
    ),
  ) as ClaudeSdkMessage[];
  let signal: AbortSignal | undefined;
  const start_agent_turn = vi.fn((_reason: string, cancel?: AbortSignal) => {
    signal = cancel;
    return new Promise<null>((resolve) =>
      cancel!.addEventListener('abort', () => resolve(null), { once: true }),
    );
  });
  const state = await live_child('pending-shutdown', { start_agent_turn });
  try {
    state.deliver({
      value: frames.find((frame) => frame.subtype === 'background_tasks_changed')!,
      done: false,
    });
    state.deliver({
      value: frames.findLast((frame) => frame.subtype === 'task_notification')!,
      done: false,
    });
    await vi.waitFor(() => expect(start_agent_turn).toHaveBeenCalledOnce());
    await state.child.stop();
    expect(signal?.aborted).toBe(true);
    expect(state.turn_endings).toHaveLength(0);
  } finally {
    await state.child.stop();
  }
});
