/** Hosted peer for the real Claude adapter's exact disconnect and rebind boundary. */
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import type { ClaudeSdkClientFactory } from '../../../conversation/backends/claude_agent_sdk/query_contracts.ts';
import { Pending } from '../../../conversation/backends/claude_agent_sdk/wire.ts';

export function create_claude_cleanup_fixture() {
  let cleanup = new Pending<void>();
  cleanup.resolve();
  let cleanup_entered = false;
  const clients: {
    session_id: string;
    resume: string | null;
    model: string | null;
    effort: string | null;
    prompts: string[];
    disconnected: boolean;
    messages: PassThrough;
  }[] = [];
  const client_factory: ClaudeSdkClientFactory = (options) => {
    assert.ok(clients.length === 0 || clients.at(-1)!.disconnected);
    const session_id = options.sessionId ?? options.resume;
    assert.ok(session_id);
    const client = {
      session_id,
      resume: options.resume ?? null,
      model: options.model ?? null,
      effort: options.effort ?? null,
      prompts: [] as string[],
      disconnected: false,
      messages: new PassThrough({ objectMode: true }),
    };
    clients.push(client);
    return {
      connect: async () => {},
      get_server_info: async () => null,
      async query(prompt) {
        if (typeof prompt === 'string') client.prompts.push(prompt);
        else
          for await (const message of prompt) {
            const content = message.message.content;
            client.prompts.push(
              typeof content === 'string'
                ? content
                : content
                    .flatMap((block) => (block.type === 'text' ? [block.text] : []))
                    .join('\n'),
            );
          }
      },
      receive_messages: () => client.messages,
      watch_user_message: () => {},
      forget_user_message: () => {},
      wait_for_user_message_admission: async () => true,
      user_message_uuids_for_result: () => new Set<string>(),
      user_message_is_settled: () => true,
      wait_for_user_message_settlement: async () => {},
      interrupt_and_cancel_queued: async () => [new Set<string>(), new Set<string>()],
      async disconnect() {
        cleanup_entered = true;
        await cleanup.promise;
        client.disconnected = true;
        client.messages.end();
      },
    };
  };
  return {
    client_factory,
    state: () => ({
      cleanup_entered,
      clients: clients.map(({ messages: _messages, ...client }) => client),
    }),
    finish_turn() {
      const client = clients.at(-1)!;
      client.messages.write({
        type: 'result',
        subtype: 'success',
        duration_ms: 1,
        duration_api_ms: 1,
        is_error: false,
        num_turns: 1,
        session_id: client.session_id,
      });
    },
    hold_cleanup() {
      cleanup_entered = false;
      cleanup = new Pending<void>();
    },
    release_cleanup() {
      cleanup.resolve();
    },
  };
}
