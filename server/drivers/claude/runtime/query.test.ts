/** Manager calls are mocked. Frames reach the client through a fake SDK Query. */
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { afterEach, expect, it, vi } from 'vitest';
import { spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import type { Options, SpawnedProcess, SDKUserMessage } from '@anthropic-ai/claude-agent-sdk';
import { WorkerSupervisor } from '../../../worker_supervision/index.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import type { BackendEventSink } from '../../../conversation/backends/contracts.ts';
import { WorkerScratch } from '../../../conversation/worker_scratch.ts';
import { worker_supervision_fixture } from '../../../conversation/backends/testing/worker_supervision_fixture.ts';
import { claude_sdk_client } from './query.ts';
import * as claude_sdk from './sdk.ts';

const { query } = vi.hoisted(() => ({
  query:
    vi.fn<
      (
        ...arguments_: Parameters<(typeof import('@anthropic-ai/claude-agent-sdk'))['query']>
      ) => ReturnType<(typeof import('@anthropic-ai/claude-agent-sdk'))['query']>
    >(),
}));

vi.mock('node:child_process', () => ({ spawn: vi.fn(), spawnSync: vi.fn() }));
vi.mock('@anthropic-ai/claude-agent-sdk', () => ({ query }));
vi.mock('../../../conversation/backends/cli_compatibility.ts', async (original) => ({
  ...(await original<typeof import('../../../conversation/backends/cli_compatibility.ts')>()),
  ensure_cli_compatible: vi.fn(),
}));
vi.mock('../../../worker_supervision/index.ts', () => ({ WorkerSupervisor: { create: vi.fn() } }));
vi.mock('../../../conversation/worker_scratch.ts', () => ({ WorkerScratch: { create: vi.fn() } }));
afterEach(() => {
  vi.resetAllMocks();
  vi.useRealTimers();
});

function setup(
  environment: Record<string, string> = {},
  can_use_tool?: Options['canUseTool'],
  custody_sink?: BackendEventSink,
) {
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
  const configured = setup_client(
    child,
    () => {
      child.exitCode = 0;
      child.stdout.end();
      child.stderr.end();
      child.emit('exit', 0, null);
      child.emit('close', 0, null);
    },
    environment,
    can_use_tool,
    custody_sink,
  );
  return { ...configured, writes, received };
}

function setup_owned_process(child: ChildProcessWithoutNullStreams) {
  return setup_client(child, () => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  });
}

function setup_client<T extends object>(
  child: T,
  close_child: () => void,
  environment: Record<string, string> = {},
  can_use_tool?: Options['canUseTool'],
  custody_sink?: BackendEventSink,
) {
  vi.mocked(spawn).mockReturnValue(child as never);
  let spawned: SpawnedProcess;
  let pumping: Promise<void> = Promise.resolve();
  let finish_reader: () => void = () => {};
  let wake_reader: () => void = () => {};
  let reader_closed = false;
  let reader_failure: unknown = null;
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
    close_child();
  });
  const interrupt = vi.fn<(options?: { cancelQueued?: boolean }) => Promise<unknown>>(async () => ({
    cancelled: [],
    still_queued: [],
  }));
  query.mockImplementation(({ prompt, options }) => {
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
          if (reader_closed) {
            if (reader_failure !== null) throw reader_failure;
            return;
          }
          await new Promise<void>((resolve) => {
            wake_reader = resolve;
          });
        }
      },
      interrupt,
      close,
      return: async () => {
        close();
        return { done: true };
      },
    } as never;
  });
  const child_processes = new ChildProcesses();
  const client = claude_sdk_client({
    child_processes,
    one_shot_children: new OneShotChildren(child_processes),
    pathToClaudeCodeExecutable: '/synthetic/claude',
    env: environment,
    worker_supervision: environment.PLAN_TICKET_ID ? ({} as WorkerSupervisionRuntime) : undefined,
    canUseTool: can_use_tool,
    custody_sink,
  });
  return {
    client,
    child,
    close,
    interrupt,
    finish_reader,
    yield_message,
    fail_reader: (error: unknown) => {
      reader_failure = error;
      finish_reader();
    },
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
    expect(WorkerSupervisor.create).not.toHaveBeenCalled();
    expect(spawn).toHaveBeenCalledExactlyOnceWith(
      '/synthetic/claude',
      [],
      expect.objectContaining({
        env: expect.not.objectContaining({ SDK_ONLY_ADDITION: 'forbidden' }),
        detached: process.platform !== 'win32',
      }),
    );
    await client.disconnect();
    await pumping();
    expect(child.kill).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllEnvs();
  }
});

it('confirms Stop only from an interrupt receipt that names both queue lists', async () => {
  const { client, interrupt, pumping } = setup();
  await client.connect();
  interrupt.mockResolvedValueOnce({ cancelled: ['queued'], still_queued: [] });
  expect(await client.interrupt_and_cancel_queued()).toEqual([new Set(['queued']), new Set()]);
  expect(interrupt).toHaveBeenLastCalledWith({ cancelQueued: true });
  interrupt.mockResolvedValueOnce({ still_queued: [] });
  interrupt.mockResolvedValueOnce(undefined);
  interrupt.mockRejectedValueOnce(new Error('control channel closed'));
  for (let attempt = 0; attempt < 3; attempt += 1)
    await expect(client.interrupt_and_cancel_queued()).rejects.toThrow(
      'claude did not confirm queued command cancellation',
    );
  vi.useFakeTimers();
  interrupt.mockReturnValueOnce(new Promise(() => {}));
  const stalled = expect(client.interrupt_and_cancel_queued()).rejects.toThrow(
    'claude did not confirm queued command cancellation',
  );
  await vi.advanceTimersByTimeAsync(5000);
  await stalled;
  vi.useRealTimers();
  await client.disconnect();
  await pumping();
});

it('admits a steer from its lifecycle frame while the adapter is still taking in an earlier message', async () => {
  const { client, yield_message, pumping } = setup();
  await client.connect();
  const reader = client.receive_messages()[Symbol.asyncIterator]();
  yield_message({ type: 'assistant', message: { content: [] } });
  expect((await reader.next()).done).toBe(false);
  // The adapter is inside take_in and does not pull the next message.
  client.watch_user_message('steer');
  yield_message({ type: 'command_lifecycle', command_uuid: 'steer', state: 'queued' });
  expect(await client.wait_for_user_message_admission('steer')).toBe(true);
  yield_message({ type: 'command_lifecycle', command_uuid: 'steer', state: 'completed' });
  await vi.waitFor(() => expect(client.user_message_is_settled('steer')).toBe(true));
  await client.disconnect();
  await pumping();
});

it('retains a failed cleanup handle until the same owned child confirms exit', async () => {
  vi.useFakeTimers();
  const { client, child, close, pumping } = setup();
  await client.connect();
  close.mockImplementation(() => {});
  const first = expect(client.disconnect()).rejects.toThrow(
    'Child process termination remains unconfirmed',
  );
  await vi.advanceTimersByTimeAsync(15_000);
  await first;
  await expect(client.connect()).rejects.toThrow(
    'a Claude transport cannot reconnect after cleanup',
  );
  child.exitCode = 0;
  child.emit('exit', 0, null);
  child.emit('close', 0, null);
  await client.disconnect();
  await pumping();
  expect(spawn).toHaveBeenCalledTimes(1);
});

it('closes an exact spawned process even when SDK query construction never returns its handle', async () => {
  const { client, child, pumping } = setup();
  const construct = query.getMockImplementation()!;
  query.mockImplementation((options) => {
    construct(options);
    throw new Error('query construction failed after spawn');
  });
  child.stdin.on('finish', () => {
    child.exitCode = 0;
    child.emit('exit', 0, null);
    child.emit('close', 0, null);
  });
  await expect(client.connect()).rejects.toThrow('query construction failed after spawn');
  await client.disconnect();
  await pumping();
  expect(spawn).toHaveBeenCalledTimes(1);
  expect(child.kill).not.toHaveBeenCalled();
});

it('bounds output drain separately after the direct child exits without signalling its retired group', async () => {
  vi.useFakeTimers();
  const { client, child, close, pumping } = setup();
  await client.connect();
  close.mockImplementation(() => {
    child.exitCode = 0;
    child.emit('exit', 0, null);
  });
  const drained = expect(client.disconnect()).rejects.toThrow(
    'Claude output drain remains unconfirmed',
  );
  await vi.advanceTimersByTimeAsync(5000);
  await drained;
  expect(child.kill).not.toHaveBeenCalled();
  child.emit('close', 0, null);
  await client.disconnect();
  await pumping();
});

it('joins supervision stop when EOF and disconnect overlap and retains scratch until proof', async () => {
  let complete_stop: () => void = () => {};
  let announce_stop: () => void = () => {};
  const stopped = new Promise<void>((resolve) => {
    complete_stop = resolve;
  });
  const began = new Promise<void>((resolve) => {
    announce_stop = resolve;
  });
  const { identity, supervisor: unit, scratch } = worker_supervision_fixture();
  const ready = Promise.withResolvers<void>();
  const custody = Promise.withResolvers<void>();
  const record_custody = vi.fn(async () => {
    await custody.promise;
  });
  unit.started.mockImplementation(async () => {
    await ready.promise;
  });
  unit.stop.mockImplementation(async (native_stop) => {
    announce_stop();
    await stopped;
    await native_stop?.();
    return { complete: true, reason: 'gone', scope: 'ticket' };
  });
  vi.mocked(WorkerSupervisor.create).mockResolvedValue(unit as never);
  vi.mocked(WorkerScratch.create).mockResolvedValue(scratch as never);
  const { client, child, finish_reader, pumping } = setup(
    { PLAN_TICKET_ID: 'synthetic-worker' },
    undefined,
    {
      report: async () => {},
      record_local_child_supervision: async (path) => {
        expect(path).toBe('/synthetic/supervision.json');
        expect(spawn).not.toHaveBeenCalled();
      },
      record_local_child_custody: record_custody,
    },
  );
  Object.assign(child, { pid: 4321 });
  let connected = false;
  const connecting = client.connect().then(() => {
    connected = true;
  });
  await vi.waitFor(() => expect(unit.started).toHaveBeenCalled());
  expect(connected).toBe(false);
  ready.resolve();
  await vi.waitFor(() => expect(record_custody).toHaveBeenCalledWith(identity));
  expect(connected).toBe(false);
  custody.resolve();
  await connecting;
  expect(await client.local_child_identity?.()).toEqual(identity);
  const stream = client.receive_messages()[Symbol.asyncIterator]();
  const next = stream.next();
  finish_reader();
  await began;
  const disconnect = client.disconnect();
  await Promise.resolve();
  expect(unit.stop).toHaveBeenCalledTimes(1);
  expect(scratch.close).not.toHaveBeenCalled();
  complete_stop();
  await Promise.all([next, disconnect]);
  await pumping();
  expect(unit.stop).toHaveBeenCalledTimes(1);
  expect(scratch.close).toHaveBeenCalledTimes(1);
});

function tool_use_assistant(id: string, parent_tool_use_id: string | null = null) {
  return {
    type: 'assistant',
    parent_tool_use_id,
    message: { content: [{ type: 'tool_use', id, name: 'Read', input: {} }] },
  };
}
function ask(toolUseID: string, signal = new AbortController().signal) {
  return query.mock.calls.at(-1)![0].options!.canUseTool!(
    'Read',
    {},
    { signal, toolUseID, requestId: `request-${toolUseID}` },
  );
}

it('holds an ask until the adapter has taken in the assistant message with its own tool use', async () => {
  const callback = vi.fn(async () => ({ behavior: 'deny' as const, message: 'scripted' }));
  const { client, yield_message, pumping } = setup({}, callback);
  await client.connect();
  const reader = client.receive_messages()[Symbol.asyncIterator]();
  const first = reader.next();
  const own = ask('own-call');
  const subagent = ask('subagent-call');
  yield_message(tool_use_assistant('other-call'));
  await first;
  const second = reader.next();
  yield_message(tool_use_assistant('own-call'));
  await second;
  // A macrotask drains every microtask, so a released ask would have answered by now.
  const pending = new Promise<'waiting'>((resolve) => setImmediate(() => resolve('waiting')));
  // Another tool use was taken in. This one is delivered but not yet taken in.
  expect(await Promise.race([own.then(() => 'answered'), pending])).toBe('waiting');
  expect(callback).not.toHaveBeenCalled();
  const third = reader.next();
  await own;
  expect(callback).toHaveBeenCalledTimes(1);
  yield_message(tool_use_assistant('subagent-call', 'parent-task'));
  await third;
  const fourth = reader.next();
  await subagent;
  expect(callback).toHaveBeenCalledTimes(2);
  yield_message(tool_use_assistant('earlier-call'));
  await fourth;
  const fifth = reader.next();
  // Its tool use was already taken in, so the ask does not wait.
  await ask('earlier-call');
  expect(callback).toHaveBeenCalledTimes(3);
  await client.disconnect();
  await fifth;
  await pumping();
});

it('denies a waiting ask on callback abort and disconnect', async () => {
  const callback = vi.fn(async () => ({ behavior: 'deny' as const, message: 'scripted' }));
  const { client, pumping } = setup({}, callback);
  await client.connect();
  const next = client.receive_messages()[Symbol.asyncIterator]().next();
  const abort = new AbortController();
  const aborted = ask('abort-call', abort.signal);
  abort.abort();
  const denied = { behavior: 'deny', message: 'The turn ended before this was answered.' };
  expect(await aborted).toEqual(denied);
  const waiting = ask('disconnect-call');
  const disconnect = client.disconnect();
  expect(await waiting).toEqual(denied);
  expect(callback).not.toHaveBeenCalled();
  await Promise.all([disconnect, next]);
  await pumping();
});

it('fails a later prompt immediately after an owned live child loses stdin', async () => {
  const native = await vi.importActual<typeof import('node:child_process')>('node:child_process');
  const owned = native.spawn(
    process.execPath,
    [
      '-e',
      'require("node:fs").closeSync(0); process.stdout.write("READY\\n"); setInterval(() => {}, 1000);',
    ],
    {
      cwd: process.env.HOME,
      env: { HOME: process.env.HOME, PATH: '/usr/bin:/bin' },
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
    },
  );
  let closed = false;
  const exited = new Promise<void>((resolve) =>
    owned.once('close', () => {
      closed = true;
      resolve();
    }),
  );
  const ready = new Promise<void>((resolve, reject) => {
    owned.stdout.once('data', (chunk: Buffer) =>
      chunk.toString() === 'READY\n'
        ? resolve()
        : reject(new Error('Unexpected owned child readiness')),
    );
    owned.once('error', reject);
  });
  const { client, pumping } = setup_owned_process(owned);
  try {
    await ready;
    await client.connect();
    await expect(client.query('first write')).rejects.toMatchObject({ code: 'EPIPE' });
    expect(owned.exitCode).toBeNull();
    let settled = false;
    const later = client.query('later write').catch((error: unknown) => {
      settled = true;
      return error;
    });
    await Promise.resolve();
    expect(settled).toBe(true);
    expect(await later).toMatchObject({ code: 'EPIPE' });
  } finally {
    await client.disconnect();
    if (!closed) owned.kill('SIGKILL');
    await exited;
    await pumping();
  }
});

it('releases a waiting ask after the adapter takes in a turn result', async () => {
  const callback = vi.fn(async () => ({ behavior: 'deny' as const, message: 'scripted' }));
  const { client, yield_message, pumping } = setup({}, callback);
  await client.connect();
  const reader = client.receive_messages()[Symbol.asyncIterator]();
  const first = reader.next();
  const after_result = ask('never-delivered');
  const result = { type: 'result', uuid: 'turn-result' };
  yield_message(result);
  expect((await first).value).toEqual(result);
  await Promise.resolve();
  expect(callback).not.toHaveBeenCalled();
  const next = reader.next();
  expect(await after_result).toEqual({ behavior: 'deny', message: 'scripted' });
  expect(callback).toHaveBeenCalledTimes(1);
  await client.disconnect();
  await next;
  await pumping();
});

it('bounds a waiting ask whose tool use never reaches the reader', async () => {
  vi.useFakeTimers();
  const callback = vi.fn(async () => ({ behavior: 'deny' as const, message: 'scripted' }));
  const { client, pumping } = setup({}, callback);
  await client.connect();
  const next = client.receive_messages()[Symbol.asyncIterator]().next();
  const answer = ask('lost-call');
  await vi.advanceTimersByTimeAsync(5000);
  expect(await answer).toEqual({ behavior: 'deny', message: 'scripted' });
  expect(callback).toHaveBeenCalledTimes(1);
  await client.disconnect();
  await next;
  await pumping();
});

it('refuses a running CLI whose handshake reports a version below the floor', async () => {
  const { client, close, pumping } = setup();
  const scripted = query.getMockImplementation()!;
  query.mockImplementation((parameters) =>
    Object.assign(scripted(parameters), {
      initializationResult: async () => ({ claude_code_version: '2.1.100' }),
    }),
  );
  await expect(client.connect()).rejects.toThrow(
    'Claude 2.1.100 at /synthetic/claude is too old. Update Claude to at least 2.1.219. Panels requires queued message cancellation on Stop.',
  );
  expect(close).toHaveBeenCalled();
  await pumping();
});

it('translates only SDK-owned numeric exit failures to the full Python process error', async () => {
  const { client, fail_reader, pumping } = setup();
  await client.connect();
  const next = client.receive_messages()[Symbol.asyncIterator]().next();
  fail_reader(
    Object.assign(new Error('Claude Code process exited with code 7. stderr: owned tail'), {
      errorClass: 'process_exited_nonzero',
      exitCode: 7,
    }),
  );
  await expect(next).rejects.toThrow(
    'Command failed with exit code 7 (exit code: 7)\nError output: Check stderr output for details',
  );
  await client.disconnect();
  await pumping();
});

it('rejects an async prompt released after stdin fails before any message is registered', async () => {
  const { client, child, received, pumping } = setup();
  await client.connect();
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  async function* delayed(): AsyncGenerator<SDKUserMessage> {
    await held;
    yield {
      type: 'user',
      message: { role: 'user', content: 'late input' },
      parent_tool_use_id: null,
      session_id: 'default',
    };
  }
  const failure = new Error('owned synthetic stdin failed');
  const pending = client.query(delayed());
  const rejected = expect(pending).rejects.toBe(failure);
  await Promise.resolve();
  child.stdin.emit('error', failure);
  release();
  await rejected;
  expect(received).toEqual([]);
  await client.disconnect();
  await pumping();
});

it('revokes an unspawned Worker when its supervision reporter fails and closes scratch', async () => {
  const { supervisor, scratch } = worker_supervision_fixture();
  vi.mocked(WorkerSupervisor.create).mockResolvedValueOnce(supervisor as never);
  vi.mocked(WorkerScratch.create).mockResolvedValueOnce(scratch as never);
  const reporting = Promise.withResolvers<void>();
  const finish_report = Promise.withResolvers<void>();
  const { client } = setup({ PLAN_TICKET_ID: 'synthetic-worker' }, undefined, {
    report: async () => {},
    record_local_child_supervision: async () => {
      reporting.resolve();
      await finish_report.promise;
      throw new Error('custody write failed');
    },
  });
  const connecting = expect(client.connect()).rejects.toThrow('custody write failed');
  await reporting.promise;
  let closed = false;
  const deadline = performance.now() / 1000 + 2;
  const closing = client.disconnect({ deadline }).then(() => {
    closed = true;
  });
  await Promise.resolve();
  expect(closed).toBe(false);
  expect(scratch.close).not.toHaveBeenCalled();
  finish_report.resolve();
  await Promise.all([connecting, closing]);
  expect(supervisor.cancel_unspawned).toHaveBeenCalledTimes(1);
  expect(supervisor.stop.mock.calls[0][1]).toEqual({ deadline });
  expect(spawn).not.toHaveBeenCalled();
  expect(scratch.close).toHaveBeenCalledTimes(1);
});

it('disconnect during SDK import prevents late Query construction and retains the connect settlement', async () => {
  const original = claude_sdk.load_claude_sdk;
  const delayed = Promise.withResolvers<Awaited<ReturnType<typeof original>>>();
  const loader = vi
    .spyOn(claude_sdk, 'load_claude_sdk')
    .mockImplementationOnce(() => delayed.promise);
  const { client } = setup();
  const connecting = expect(client.connect()).rejects.toThrow(
    'startup stopped before SDK construction',
  );
  try {
    await vi.waitFor(() => expect(loader).toHaveBeenCalled());
    await client.disconnect();
    let settled = false;
    const actual = client.settled!().then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);
    delayed.resolve(await original());
    await connecting;
    await actual;
    expect(query).not.toHaveBeenCalled();
    expect(spawn).not.toHaveBeenCalled();
  } finally {
    delayed.resolve(await original());
    loader.mockRestore();
    await client.disconnect();
  }
});
