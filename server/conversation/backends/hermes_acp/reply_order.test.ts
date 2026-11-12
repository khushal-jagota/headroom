import { expect, test } from 'vitest';
import type { AnyMessage } from '@agentclientprotocol/sdk/unstable';
import type { ConversationMessageFiles } from '../../message_files.ts';
import { HermesAcpClient } from './client.ts';
import { Deferred, HermesEvents } from './events.ts';
import type { HermesTurn } from './events.ts';
import { HermesMessages } from './messages.ts';

test('a later notification cannot overtake asynchronous prompt-reply effects', async () => {
  let input!: ReadableStreamDefaultController<AnyMessage>;
  const writes: AnyMessage[] = [];
  const usage_started = new Deferred<void>();
  const release_usage = new Deferred<void>();
  const observed: string[] = [];
  const events = new HermesEvents(
    {
      async report(_turn, event) {
        if (event.constructor.name === 'TokenUsageReported') {
          usage_started.resolve();
          await release_usage.promise;
        }
        observed.push(event.constructor.name);
      },
    },
    new HermesMessages({} as ConversationMessageFiles, 'synthetic'),
  );
  const client = new HermesAcpClient(
    {
      readable: new ReadableStream<AnyMessage>({
        start(controller) {
          input = controller;
        },
      }),
      writable: new WritableStream<AnyMessage>({
        write(message) {
          writes.push(message);
        },
      }),
    },
    { session_update: (params) => events.session_update(params) },
  );
  let turn!: HermesTurn;
  try {
    const request = client.begin_request(
      'session/prompt',
      { sessionId: 'synthetic', prompt: [] },
      {
        after_response: async () => {
          await turn.ending_reporter;
        },
      },
    );
    turn = events.begin(
      { conversation_id: 'synthetic', turn_number: 1 },
      request.response,
      false,
    );
    turn.ending_reporter = events.report_ending(turn, () => null);
    await request.written;
    const written = writes[0]!;
    if (!('id' in written)) throw new Error('Expected SDK request');
    input.enqueue({
      jsonrpc: '2.0',
      id: written.id,
      result: {
        stopReason: 'end_turn',
        usage: { inputTokens: 2, outputTokens: 3, totalTokens: 5 },
      },
    });
    input.enqueue({
      jsonrpc: '2.0',
      method: 'session/update',
      params: {
        sessionId: 'synthetic',
        update: {
          sessionUpdate: 'agent_message_chunk',
          content: { type: 'text', text: 'late text must not enter old turn' },
        },
      },
    });
    await usage_started.promise;
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(observed).toEqual([]);
    release_usage.resolve();
    await turn.ending_reporter;
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(observed).toEqual(['TokenUsageReported', 'TurnEnded']);
    expect(events.turn).toBeNull();
  } finally {
    release_usage.resolve();
    await client.stop();
  }
});
