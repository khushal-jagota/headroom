/** Hosted peer for the real Claude adapter's exact disconnect and rebind boundary. */
import assert from 'node:assert/strict';
import { PassThrough } from 'node:stream';
import type { ClaudeSdkClientFactory } from '../../../drivers/claude/runtime/query_contracts.ts';
import { Deferred } from '../../../core/deferred.ts';

export function create_claude_cleanup_fixture() {
  let cleanup = new Deferred<void>();
  cleanup.resolve();
  let cleanup_entered = false;
  const results = new Map<string, Set<string>>();
  const clients: {
    session_id: string;
    resume: string | null;
    model: string | null;
    effort: string | null;
    prompts: string[];
    unanswered_prompt_uuids: string[];
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
      unanswered_prompt_uuids: [] as string[],
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
            if (message.uuid) client.unanswered_prompt_uuids.push(message.uuid);
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
      // Each result names the prompts it answers, as Claude does.
      user_message_uuids_for_result: (uuid) => results.get(uuid ?? '') ?? new Set<string>(),
      user_message_is_settled: () => true,
      wait_for_user_message_settlement: async () => {},
      interrupt_and_cancel_queued: async () => [new Set<string>(), new Set<string>()],
      async apply_reasoning_effort(level) {
        client.effort = level;
      },
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
      clients: clients.map(
        ({ messages: _messages, unanswered_prompt_uuids: _unanswered, ...client }) => client,
      ),
    }),
    finish_turn() {
      const client = clients.at(-1)!;
      const uuid = `cleanup-result-${results.size + 1}`;
      results.set(uuid, new Set(client.unanswered_prompt_uuids.splice(0)));
      client.messages.write({
        uuid,
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
      cleanup = new Deferred<void>();
    },
    release_cleanup() {
      cleanup.resolve();
    },
  };
}
