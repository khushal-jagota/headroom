import { readFileSync } from 'node:fs';
import { expect, test, vi } from 'vitest';
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
  const write_gates: Promise<void>[] = [];
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
      await (write_gates.shift() ?? write_gate);
    },
  });
  const client = new HermesAcpClient(sdk.ndJsonStream(output, source), handlers);
  return {
    client,
    writes,
    hold_each_write(...gates: Promise<void>[]) {
      write_gates.push(...gates);
    },
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

test('official ACP SDK pin, consumed internals, and imports preserve the parent environment', async () => {
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
  const root = import.meta.resolve('@agentclientprotocol/sdk/unstable');
  const rpc = await import(new URL('../../core/jsonrpc.js', root).href);
  const schemas = await import(new URL('./schema/zod.gen.js', root).href);
  const builder = rpc.Connection.builder();
  expect(typeof builder.withHandler).toBe('function');
  expect(typeof builder.connect).toBe('function');
  expect(builder.withHandler({ handleMessage: () => rpc.Handled.yes() })).toBe(builder);
  for (const symbol of ['sendRequest', 'sendNotification', 'close']) {
    expect(typeof rpc.Connection.prototype[symbol]).toBe('function');
  }
  for (const symbol of ['isRequestMessage', 'isNotificationMessage']) {
    expect(typeof rpc[symbol]).toBe('function');
  }
  expect(rpc.Handled.yes()).toEqual({ handled: true });
  expect(typeof sdk.ndJsonStream).toBe('function');
  expect(typeof sdk.RequestError.methodNotFound).toBe('function');
  for (const symbol of [
    'zSessionNotification',
    'zRequestPermissionRequest',
    'zInitializeResponse',
    'zNewSessionResponse',
    'zLoadSessionResponse',
    'zSetSessionModeResponse',
    'zSetSessionConfigOptionResponse',
    'zPromptResponse',
  ])
    expect(typeof schemas[symbol].parse).toBe('function');
  const { client } = wire(no_handlers); // Connection accepts our typed handleMessage contract.
  expect(client.signal).toBeInstanceOf(AbortSignal);
  expect(client.closed).toBeInstanceOf(Promise);
  await client.stop();
});

test('a slow split notification preserves wire and response order beyond fifteen seconds', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
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
    await vi.advanceTimersByTimeAsync(16000);
    expect(client.signal.aborted).toBe(false);
    expect(order).toEqual(['notification started']);
    gate.resolve();
    await prompt.response;
    await tick();
    expect(order).toEqual(['notification started', 'notification finished', 'response']);
  } finally {
    gate.resolve();
    await client.stop();
    vi.useRealTimers();
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

test('one timeout and one cancel leave another pending request and later writes usable', async () => {
  const { client, writes, send } = wire(no_handlers);
  try {
    const first = client.begin_request('timed-out', {}, { timeout_ms: 5 });
    const timed_out = expect(first.response).rejects.toBeInstanceOf(Error);
    await first.written;
    const second = client.begin_request('unaffected', {});
    await second.written;
    await timed_out;
    const cancelled = client.begin_request('cancelled', {});
    const rejected = expect(cancelled.response).rejects.toBeInstanceOf(Error);
    await cancelled.written;
    cancelled.cancel();
    await rejected;
    const index = writes.findIndex(
      (message) => 'method' in message && message.method === 'unaffected',
    );
    send(response(writes, index));
    expect(await second.response).toEqual({ value: index });
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

test('SDK write receipts settle separately for the same method and params object', async () => {
  const first_gate = deferred<void>();
  const second_gate = deferred<void>();
  const { client, writes, send, hold_each_write } = wire(no_handlers);
  hold_each_write(first_gate.promise, second_gate.promise);
  const params = { shared: true };
  try {
    const first = client.begin_request('same-method', params);
    const second = client.begin_request('same-method', params);
    let second_written = false;
    void second.written.then(() => {
      second_written = true;
    });
    await tick();
    first_gate.resolve();
    await first.written;
    await tick();
    expect(second_written).toBe(false);
    second_gate.resolve();
    await second.written;
    expect(writes).toHaveLength(2);
    const first_message = writes[0]!;
    const second_message = writes[1]!;
    expect(
      'id' in first_message && 'id' in second_message && first_message.id !== second_message.id,
    ).toBe(true);
    send(response(writes, 0));
    send(response(writes, 1));
    await Promise.all([first.response, second.response]);
  } finally {
    first_gate.resolve();
    second_gate.resolve();
    await client.stop();
  }
}, 1000);

test('closing a wire releases admission while its sink stays slow', async () => {
  const gate = deferred<void>();
  const entered = deferred<void>();
  const { client, send } = wire({
    async session_update() {
      entered.resolve();
      await gate.promise;
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
  await entered.promise;
  await client.stop();
  expect(client.signal.aborted).toBe(true);
  gate.resolve();
});
