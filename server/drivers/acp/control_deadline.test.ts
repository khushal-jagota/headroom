import { expect, test, vi } from 'vitest';
import type { AnyMessage } from '@agentclientprotocol/sdk/unstable';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import { NeedsRebind, SessionLoadFailed } from '../../conversation/backends/contracts.ts';
import { AcpClient } from './client.ts';
import { AcpSessionConfiguration } from './session_configuration.ts';
import { Deferred } from '../../core/deferred.ts';

test('hung ordered persistence bounds configuration without failing other requests or the wire', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  let input!: ReadableStreamDefaultController<AnyMessage>;
  const writes: AnyMessage[] = [];
  const entered = new Deferred<void>();
  const persisted = new Deferred<void>();
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
    {
      async session_update() {
        entered.resolve();
        await persisted.promise;
      },
    },
  );
  const config = new AcpSessionConfiguration(client, 'Synthetic');
  config.bind('synthetic', {
    configOptions: [
      {
        id: 'model',
        name: 'Model',
        category: 'model',
        type: 'select',
        currentValue: 'original',
        options: [],
      },
    ],
  });
  try {
    input.enqueue({
      jsonrpc: '2.0',
      method: 'session/update',
      params: {
        sessionId: 'synthetic',
        update: {
          sessionUpdate: 'agent_message_chunk',
          content: { type: 'image', mimeType: 'image/png', data: 'eA==' },
        },
      },
    });
    await entered.promise;
    const change = expect(config.apply_values('other', null)).rejects.toBeInstanceOf(NeedsRebind);
    const unaffected = client.begin_request('unaffected', {});
    await unaffected.written;
    for (const frame of writes)
      if ('id' in frame && 'method' in frame)
        input.enqueue({ jsonrpc: '2.0', id: frame.id, result: { value: 'alive' } });
    await vi.advanceTimersByTimeAsync(15000);
    await change;
    expect(client.signal.aborted).toBe(false);
    expect(config.wire_broken).toBe(false);
    expect(
      writes.filter((frame) => 'method' in frame && frame.method === '$/cancel_request'),
    ).toHaveLength(1);
    const startup = expect(
      config.start_values({
        access: 'full',
        // A model the session does not report as current, so startup must ask for it.
        model: 'other',
        reasoning_effort: null,
      } as ResolvedConversationStart),
    ).rejects.toBeInstanceOf(SessionLoadFailed);
    await vi.advanceTimersByTimeAsync(15000);
    await startup;
    expect(client.signal.aborted).toBe(false);
    persisted.resolve();
    await expect(unaffected.response).resolves.toEqual({ value: 'alive' });
  } finally {
    persisted.resolve();
    await client.stop();
    vi.useRealTimers();
  }
});
