import { parse_backend_instance_id } from '../../core/identity.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
/** A terminal reader failure must not prevent cleanup of its exact injected client. */
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { expect, it, vi } from 'vitest';
import { ResolvedConversationStart } from '../../../conversation/contracts.ts';
import {
  BackendStreamEnded,
  BackendSpawnFailed,
  BackendInitializationTimeout,
  NeedsRebind,
  PromptWriteFailed,
  SessionLoadFailed,
  TurnEnded,
} from '../../../conversation/backends/contracts.ts';
import { text_message_content } from '../../../conversation/message_content.ts';
import { ClaudeAgentSdkBackendChild } from './adapter.ts';
import type { ClaudeSdkClient, ClaudeSdkMessage } from './query_contracts.ts';
import { Deferred } from '../../../core/deferred.ts';
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
  const child = new ClaudeAgentSdkBackendChild({
    child_processes,
    worker_supervision: {} as WorkerSupervisionRuntime,
    one_shot_children: new OneShotChildren(child_processes),
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
      const child = new ClaudeAgentSdkBackendChild({
        child_processes,
        one_shot_children: new OneShotChildren(child_processes),
        worker_supervision: {} as WorkerSupervisionRuntime,
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
  const stream_ended = new Deferred<void>();
  const child_processes = new ChildProcesses();
  const child = new ClaudeAgentSdkBackendChild({
    child_processes,
    one_shot_children: new OneShotChildren(child_processes),
    worker_supervision: {} as WorkerSupervisionRuntime,
    launch: { claude_executable: '/synthetic/claude' },
    resolved_start: start,
    client_factory: () => client,
    event_sink: {
      report: async (_turn, event) => {
        if (event instanceof TurnEnded) turn_endings.push(event);
        if (event instanceof BackendStreamEnded) stream_ended.resolve();
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
  return { child, content, prompt, deliver, turn_endings, stream_ended: stream_ended.promise };
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
