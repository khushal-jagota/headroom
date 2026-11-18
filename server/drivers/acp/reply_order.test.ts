import { expect, test } from 'vitest';
import type { AnyMessage, PromptResponse } from '@agentclientprotocol/sdk/unstable';
import type { ConversationMessageFiles } from '../../conversation/message_files.ts';
import { AcpClient } from './client.ts';
import { Deferred } from '../../core/deferred.ts';
import { AcpEvents } from './events.ts';
import type { AcpTurn } from './events.ts';
import { AcpMessages } from './messages.ts';

test('a later notification cannot overtake asynchronous prompt-reply effects', async () => {
  let input!: ReadableStreamDefaultController<AnyMessage>;
  const writes: AnyMessage[] = [];
  const usage_started = new Deferred<void>();
  const release_usage = new Deferred<void>();
  const observed: string[] = [];
  const events = new AcpEvents(
    {
      async report(_turn, event) {
        if (event.constructor.name === 'TokenUsageReported') {
          usage_started.resolve();
          await release_usage.promise;
        }
        observed.push(event.constructor.name);
      },
    },
    new AcpMessages({} as ConversationMessageFiles, 'synthetic'),
    { label: 'Synthetic', launch: () => ({ argv: [], environment_overrides: [] }) },
    () => {},
  );
  const client = new AcpClient(
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
  let turn!: AcpTurn;
  try {
    const request = client.begin_request<PromptResponse>(
      'session/prompt',
      { sessionId: 'synthetic', prompt: [] },
      {
        after_response: async () => {
          await turn.ending_reporter;
        },
      },
    );
    turn = events.begin({ conversation_id: 'synthetic', turn_number: 1 }, request, 'synthetic');
    turn.ending_reporter = events.report_ending(turn, {
      standard_error_tail: () => null,
      wire_ended: () => false,
      break_wire: () => {},
    });
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
