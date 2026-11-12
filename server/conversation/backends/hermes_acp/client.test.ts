import { readFileSync } from 'node:fs';
import { expect, test } from 'vitest';
import type { AnyMessage } from '@agentclientprotocol/sdk/unstable';

const before_import = { ...process.env };
const sdk = await import('@agentclientprotocol/sdk/unstable');
const { HermesAcpClient } = await import('./client.ts');
const after_import = { ...process.env };
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function wire(handlers: ConstructorParameters<typeof HermesAcpClient>[1]) {
  let input!: ReadableStreamDefaultController<Uint8Array>;
  let fail_write = false;
  let write_gate: Promise<void> | null = null;
  const writes: AnyMessage[] = [];
  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      input = controller;
    },
  });
  const output = new WritableStream<Uint8Array>({
    async write(bytes) {
      if (fail_write) throw new Error('synthetic broken write');
      writes.push(JSON.parse(new TextDecoder().decode(bytes)) as AnyMessage);
      await write_gate;
    },
  });
  const client = new HermesAcpClient(sdk.ndJsonStream(output, source), handlers);
  return {
    client,
    writes,
    hold_writes(gate: Promise<void>) {
      write_gate = gate;
    },
    fail() {
      fail_write = true;
    },
    send(message: unknown, split = false) {
      const bytes = new TextEncoder().encode(JSON.stringify(message) + '\n');
      if (split) input.enqueue(bytes.slice(0, 9));
      input.enqueue(split ? bytes.slice(9) : bytes);
    },
  };
}
function response(
  writes: AnyMessage[],
  index = 0,
): { jsonrpc: string; id: string | number | null; result: unknown } {
  const request = writes[index]!;
  if (!('id' in request)) throw new Error('expected request');
  return {
    jsonrpc: '2.0',
    id: request.id,
    result:
      'method' in request && request.method === 'session/prompt'
        ? { stopReason: 'end_turn' }
        : { value: index },
  };
}
const no_handlers = {
  async session_update() {},
  async request_permission() {
    return { answer: Promise.resolve({ outcome: { outcome: 'cancelled' as const } }) };
  },
};

test('official ACP SDK pin and imports preserve the parent environment', () => {
  const metadata = JSON.parse(
    readFileSync(
      new URL('../../../package.json', import.meta.resolve('@agentclientprotocol/sdk/unstable')),
      'utf8',
    ),
  ) as { version: string };
  const manifest = JSON.parse(
    readFileSync(new URL('../../../package.json', import.meta.url), 'utf8'),
  ) as {
    dependencies: Record<string, string>;
  };
  expect(metadata.version).toBe('2.6.0');
  expect(manifest.dependencies['@agentclientprotocol/sdk']).toBe('2.6.0');
  expect(after_import).toEqual(before_import);
});

test('split notification finishes before the following prompt response', async () => {
  const gate = deferred<void>();
  const order: string[] = [];
  const { client, writes, send } = wire({
    ...no_handlers,
    async session_update() {
      order.push('notification started');
      await gate.promise;
      order.push('notification finished');
    },
  });
  try {
    const prompt = client.begin_request('session/prompt', { sessionId: 'synthetic', prompt: [] });
    await prompt.written;
    send(
      {
        jsonrpc: '2.0',
        method: 'session/update',
        params: {
          sessionId: 'synthetic',
          update: {
            sessionUpdate: 'agent_message_chunk',
            content: { type: 'text', text: 'hello' },
          },
        },
      },
      true,
    );
    send(response(writes));
    void prompt.response.then(() => order.push('response'));
    await tick();
    expect(order).toEqual(['notification started']);
    gate.resolve();
    await prompt.response;
    await tick();
    expect(order).toEqual(['notification started', 'notification finished', 'response']);
  } finally {
    gate.resolve();
    await client.stop();
  }
});

test('parked permission does not block later notifications or the prompt reply', async () => {
  const answer = deferred<{ outcome: { outcome: 'cancelled' } }>();
  const order: string[] = [];
  const { client, writes, send, hold_writes } = wire({
    async session_update() {
      order.push('notification');
    },
    async request_permission() {
      order.push('permission');
      return { answer: answer.promise };
    },
  });
  try {
    const pending = client.begin_request('session/prompt', { sessionId: 'synthetic', prompt: [] });
    await pending.written;
    send({
      jsonrpc: '2.0',
      id: 'ask',
      method: 'session/request_permission',
      params: {
        sessionId: 'synthetic',
        toolCall: { toolCallId: 'tool', title: 'synthetic' },
        options: [],
      },
    });
    send({
      jsonrpc: '2.0',
      method: 'session/update',
      params: {
        sessionId: 'synthetic',
        update: {
          sessionUpdate: 'agent_thought_chunk',
          content: { type: 'text', text: 'private' },
        },
      },
    });
    send(response(writes));
    await pending.response;
    expect(order).toEqual(['permission', 'notification']);
    expect(writes).toHaveLength(1);
    const gate = deferred<void>();
    hold_writes(gate.promise);
    let delivered = false;
    const receipt = client.response_written('ask').then(() => {
      delivered = true;
    });
    answer.resolve({ outcome: { outcome: 'cancelled' } });
    await tick();
    expect(delivered).toBe(false);
    gate.resolve();
    await receipt;
    expect(delivered).toBe(true);
    expect(writes[1]).toMatchObject({ id: 'ask', result: { outcome: { outcome: 'cancelled' } } });
  } finally {
    answer.resolve({ outcome: { outcome: 'cancelled' } });
    await client.stop();
  }
});

test('one timeout leaves a second request and later writes usable', async () => {
  const { client, writes, send } = wire(no_handlers);
  try {
    const first = client.begin_request('timed-out', {}, { timeout_ms: 5 });
    const timed_out = expect(first.response).rejects.toBeInstanceOf(Error);
    await first.written;
    const second = client.begin_request('unaffected', {});
    await second.written;
    await timed_out;
    send(response(writes, 1));
    expect(await second.response).toEqual({ value: 1 });
    await client.notify('still-open', {});
    expect(writes.at(-1)).toMatchObject({ method: 'still-open' });
  } finally {
    await client.stop();
  }
});

test('write failure settles pending and later requests and shutdown is idempotent', async () => {
  const { client, fail } = wire(no_handlers);
  const first = client.begin_request('pending', {});
  await first.written;
  const first_failed = expect(first.response).rejects.toBeInstanceOf(Error);
  fail();
  const failed = client.begin_request('fails-write', {});
  const write_failed = expect(failed.written).rejects.toBeInstanceOf(Error);
  const response_failed = expect(failed.response).rejects.toBeInstanceOf(Error);
  await Promise.all([first_failed, write_failed, response_failed]);
  const later = client.begin_request('later', {});
  await expect(later.written).rejects.toBeInstanceOf(Error);
  await expect(later.response).rejects.toBeInstanceOf(Error);
  await client.stop();
  await client.stop();
});

test('notification failure is logged once and does not prevent cleanup', async () => {
  const errors: unknown[] = [];
  const { client, send } = wire({
    ...no_handlers,
    async session_update() {
      throw new Error('synthetic handler failure');
    },
    log(error: unknown) {
      errors.push(error);
    },
  });
  send({
    jsonrpc: '2.0',
    method: 'session/update',
    params: {
      sessionId: 'synthetic',
      update: { sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'private' } },
    },
  });
  await tick();
  await client.stop();
  expect(errors).toHaveLength(1);
});

test('malformed input releases admission and official unstable v1 parsing retains usage', async () => {
  const errors: unknown[] = [];
  const { client, writes, send } = wire({ ...no_handlers, log: (error) => errors.push(error) });
  try {
    const prompt = client.begin_request('session/prompt', { sessionId: 'synthetic', prompt: [] });
    await prompt.written;
    send({
      jsonrpc: '2.0',
      method: 'session/update',
      params: { sessionId: 'synthetic', update: { sessionUpdate: 'agent_message_chunk' } },
    });
    const reply = response(writes);
    reply.result = {
      stopReason: 'end_turn',
      usage: { inputTokens: 2, outputTokens: 3, totalTokens: 5 },
    };
    send(reply);
    await expect(prompt.response).resolves.toMatchObject({
      usage: { inputTokens: 2, outputTokens: 3, totalTokens: 5 },
    });
    expect(errors).toHaveLength(1);
  } finally {
    await client.stop();
  }
});
