/** A terminal reader failure must not prevent cleanup of its exact injected client. */
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { expect, it, vi } from 'vitest';
import { ResolvedConversationStart } from '../../contracts.ts';
import { BackendStreamEnded } from '../contracts.ts';
import { ClaudeAgentSdkBackendChild } from './adapter.ts';
import type { ClaudeSdkClient, ClaudeSdkMessage } from './query_contracts.ts';
import { Pending } from './wire.ts';

it('clears a failed reader and disconnects once across repeated stop calls', async () => {
  const next = new Pending<IteratorResult<ClaudeSdkMessage>>();
  const failedReport = new Pending<void>();
  const failure = Object.assign(new Error('database is locked'), { code: 'SQLITE_BUSY' });
  const disconnect = vi.fn(async () => {});
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
    disconnect,
  };
  const start = new ResolvedConversationStart({
    conversation_id: 'failed-reader',
    backend_key: 'claude',
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
    await child.stop();
    await child.stop();
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(logged).toHaveBeenCalledExactlyOnceWith(
      'conversation failed-reader: Claude message reader failed during cleanup',
      failure,
    );
  } finally {
    logged.mockRestore();
  }
});
