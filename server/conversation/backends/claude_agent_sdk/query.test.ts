/** Every process and system-manager boundary is mocked before the client loads. */
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { afterEach, expect, it, vi } from 'vitest';
import { spawn } from 'node:child_process';
import { query } from '@anthropic-ai/claude-agent-sdk';
import type { Options, SpawnedProcess, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import { WorkerUnit } from '../../worker_unit/index.ts';
import { claude_sdk_client } from './query.ts';

vi.mock('node:child_process', () => ({ spawn: vi.fn() }));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query: vi.fn() }));
vi.mock('../cli_compatibility.ts', () => ({ ensure_cli_compatible: vi.fn() }));
vi.mock('../../worker_unit/index.ts', () => ({ WorkerUnit: { create: vi.fn() } }));
afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});

function setup(environment: Record<string, string> = {}, can_use_tool?: Options['canUseTool']) {
  const writes: ((cause?: Error | null) => void)[] = [];
  const received: string[] = [];
  const child = Object.assign(new EventEmitter(), {
    stdin: new Writable({
      write(chunk, _encoding, callback) {
        received.push(chunk.toString());
        writes.push(callback);
      },
    }),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    killed: false,
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    kill: vi.fn(() => true),
  });
  vi.mocked(spawn).mockReturnValue(child as never);
  let spawned: SpawnedProcess;
  let pumping: Promise<void> = Promise.resolve();
  let finish_reader: () => void = () => {};
  let wake_reader: () => void = () => {};
  let reader_closed = false;
  const reader_values: Record<string, unknown>[] = [];
  finish_reader = () => {
    reader_closed = true;
    wake_reader();
  };
  const yield_message = (message: Record<string, unknown>) => {
    reader_values.push(message);
    wake_reader();
  };
  const close = vi.fn(() => {
    finish_reader();
    child.exitCode = 0;
    child.stdout.end();
    child.stderr.end();
    child.emit('close', 0, null);
  });
  vi.mocked(query).mockImplementation(({ prompt, options }) => {
    spawned = options!.spawnClaudeCodeProcess!({
      command: '/synthetic/claude',
      args: [],
      env: { SDK_ONLY_ADDITION: 'forbidden' },
      signal: new AbortController().signal,
    });
    spawned.stdout.resume();
    pumping = (async () => {
      for await (const value of prompt as AsyncIterable<SDKUserMessage>)
        spawned.stdin.write(JSON.stringify(value) + '\n');
    })();
    return {
      initializationResult: async () => ({}),
      async *[Symbol.asyncIterator]() {
        while (true) {
          if (reader_values.length) {
            yield reader_values.shift()!;
            continue;
          }
          if (reader_closed) return;
          await new Promise<void>((resolve) => {
            wake_reader = resolve;
          });
        }
      },
      interrupt: async () => {
        spawned.stdin.write(
          JSON.stringify({
            type: 'control_request',
            request_id: 'cancel-id',
            request: { subtype: 'interrupt', cancel_queued: true },
          }) + '\n',
        );
        return { cancelled: [], still_queued: [] };
      },
      close,
      return: async () => {
        close();
        return { done: true };
      },
    } as never;
  });
  const client = claude_sdk_client({
    pathToClaudeCodeExecutable: '/synthetic/claude',
    env: environment,
    canUseTool: can_use_tool,
  });
  return {
    client,
    child,
    writes,
    received,
    close,
    finish_reader,
    yield_message,
    pumping: () => pumping,
  };
}

it('waits for actual stdin completion and does not inherit a Worker containment identity', async () => {
  vi.stubEnv('PLAN_TICKET_ID', 'inherited-worker');
  const { client, child, writes, pumping } = setup();
  try {
    await client.connect();
    let completed = false;
    const delivery = client.query('/compact').then(() => {
      completed = true;
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(completed).toBe(false);
    expect(writes).toHaveLength(1);
    writes.shift()!();
    await delivery;
    expect(WorkerUnit.create).not.toHaveBeenCalled();
    expect(spawn).toHaveBeenCalledExactlyOnceWith(
      '/synthetic/claude',
      [],
      expect.objectContaining({
        env: expect.not.objectContaining({ SDK_ONLY_ADDITION: 'forbidden' }),
      }),
    );
    await client.disconnect();
    await pumping();
    expect(child.kill).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllEnvs();
  }
});

it('validates the correlated raw cancel payload before the SDK can filter malformed members', async () => {
  const { client, child, writes, pumping } = setup();
  await client.connect();
  child.stdout.write(
    '{"type":"system","subtype":"init","capabilities":["interrupt_cancel_queued_v1"]}\n',
  );
  const cancel = client.interrupt_and_cancel_queued();
  const rejected = expect(cancel).rejects.toThrow(
    'claude did not confirm queued command cancellation',
  );
  writes.shift()!();
  child.stdout.write(
    '{"type":"control_response","response":{"request_id":"cancel-id","response":{"cancelled":[],"still_queued":[9]}}}\n',
  );
  await rejected;
  await client.disconnect();
  await pumping();
});

it('retains a failed cleanup handle until the same owned child confirms exit', async () => {
  vi.useFakeTimers();
  const { client, child, close, pumping } = setup();
  await client.connect();
  close.mockImplementation(() => {});
  const first = expect(client.disconnect()).rejects.toThrow(
    'Claude process termination remains unconfirmed',
  );
  await vi.advanceTimersByTimeAsync(15_000);
  await first;
  await expect(client.connect()).rejects.toThrow(
    'a Claude transport cannot reconnect after cleanup',
  );
  child.exitCode = 0;
  child.emit('close', 0, null);
  await client.disconnect();
  await pumping();
  expect(spawn).toHaveBeenCalledTimes(1);
});

it('closes an exact spawned process even when SDK query construction never returns its handle', async () => {
  const { client, child, pumping } = setup();
  const construct = vi.mocked(query).getMockImplementation()!;
  vi.mocked(query).mockImplementation((options) => {
    construct(options);
    throw new Error('query construction failed after spawn');
  });
  child.stdin.on('finish', () => {
    child.exitCode = 0;
    child.emit('close', 0, null);
  });
  await expect(client.connect()).rejects.toThrow('query construction failed after spawn');
  await client.disconnect();
  await pumping();
  expect(spawn).toHaveBeenCalledTimes(1);
  expect(child.kill).not.toHaveBeenCalled();
});

it('joins one owned unit stop when EOF and disconnect overlap', async () => {
  let complete_stop: () => void = () => {};
  let announce_stop: () => void = () => {};
  const stopped = new Promise<void>((resolve) => {
    complete_stop = resolve;
  });
  const began = new Promise<void>((resolve) => {
    announce_stop = resolve;
  });
  const unit = {
    environment: {},
    argv: (original: readonly string[]) => original,
    stop: vi.fn(async () => {
      announce_stop();
      await stopped;
    }),
  };
  vi.mocked(WorkerUnit.create).mockResolvedValue(unit as never);
  const { client, finish_reader, pumping } = setup({ PLAN_TICKET_ID: 'synthetic-worker' });
  await client.connect();
  const stream = client.receive_messages()[Symbol.asyncIterator]();
  const next = stream.next();
  finish_reader();
  await began;
  const disconnect = client.disconnect();
  await Promise.resolve();
  expect(unit.stop).toHaveBeenCalledTimes(1);
  complete_stop();
  await Promise.all([next, disconnect]);
  await pumping();
  expect(unit.stop).toHaveBeenCalledTimes(1);
});

it('preserves both native assistant/permission orders without waiting for SDK-filtered frames', async () => {
  for (const assistant_first of [true, false]) {
    const callback = vi.fn(async () => ({ behavior: 'deny' as const, message: 'scripted' }));
    const { client, child, yield_message, pumping } = setup({}, callback);
    await client.connect();
    const reader = client.receive_messages()[Symbol.asyncIterator]();
    const first = reader.next();
    const unknown = { unknown_extension_without_type: true };
    const assistant = {
      type: 'assistant',
      uuid: 'assistant-order',
      message: { model: 'test', content: [] },
    };
    const request = {
      type: 'control_request',
      request_id: 'permission-order',
      request: { subtype: 'can_use_tool' },
    };
    const frames = [
      { type: 'keep_alive' },
      { type: 'transcript_mirror' },
      { type: 'system', subtype: 'session_state_changed', sdk_host_only: true },
      unknown,
      ...(assistant_first ? [assistant, request] : [request, assistant]),
    ];
    child.stdout.write(frames.map((frame) => JSON.stringify(frame) + '\n').join(''));
    const options = vi.mocked(query).mock.calls.at(-1)![0].options!;
    const permission = options.canUseTool!(
      'Read',
      {},
      { signal: new AbortController().signal, toolUseID: 'call', requestId: 'permission-order' },
    );
    yield_message(unknown);
    yield_message(assistant);
    expect((await first).value).toEqual(assistant);
    if (assistant_first) {
      expect(callback).not.toHaveBeenCalled();
      const next = reader.next();
      await permission;
      expect(callback).toHaveBeenCalledTimes(1);
      await client.disconnect();
      await next;
    } else {
      // The control request precedes this assistant: its callback does not wait for it.
      await permission;
      expect(callback).toHaveBeenCalledTimes(1);
      const next = reader.next();
      await client.disconnect();
      await next;
    }
    await pumping();
  }
});

it('releases a permission barrier on callback abort and disconnect', async () => {
  const callback = vi.fn(async () => ({ behavior: 'deny' as const, message: 'scripted' }));
  const { client, child, yield_message, pumping } = setup({}, callback);
  await client.connect();
  const reader = client.receive_messages()[Symbol.asyncIterator]();
  const first = reader.next();
  const assistant = { type: 'assistant', uuid: 'before-abort' };
  child.stdout.write(
    JSON.stringify(assistant) +
      '\n' +
      JSON.stringify({
        type: 'control_request',
        request_id: 'abort-order',
        request: { subtype: 'can_use_tool' },
      }) +
      '\n',
  );
  const options = vi.mocked(query).mock.calls.at(-1)![0].options!;
  const abort = new AbortController();
  const permission = options.canUseTool!(
    'Read',
    {},
    { signal: abort.signal, toolUseID: 'call', requestId: 'abort-order' },
  );
  yield_message(assistant);
  await first;
  abort.abort();
  expect(await permission).toEqual({
    behavior: 'deny',
    message: 'The turn ended before this was answered.',
  });
  expect(callback).not.toHaveBeenCalled();
  child.stdout.write(
    JSON.stringify({
      type: 'control_request',
      request_id: 'disconnect-order',
      request: { subtype: 'can_use_tool' },
    }) + '\n',
  );
  const waiting = options.canUseTool!(
    'Read',
    {},
    { signal: new AbortController().signal, toolUseID: 'call', requestId: 'disconnect-order' },
  );
  const disconnect = client.disconnect();
  expect(await waiting).toEqual({
    behavior: 'deny',
    message: 'The turn ended before this was answered.',
  });
  const next = reader.next();
  await Promise.all([disconnect, next]);
  await pumping();
});
