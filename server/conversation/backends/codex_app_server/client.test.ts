import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, expect, test, vi } from 'vitest';
import { CodexAppServerClient, CodexWireFailed, raw_json_result } from './client.ts';
import { spawn_codex_process } from './process.ts';
import type { OwnedCodexProcess } from './process.ts';

vi.mock('./process.ts', async (original) => ({
  ...(await original<typeof import('./process.ts')>()),
  spawn_codex_process: vi.fn(),
}));
afterEach(() => vi.restoreAllMocks());

function fake() {
  const child = Object.assign(new EventEmitter(), {
    exitCode: null as number | null,
    signalCode: null,
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
  let exit!: () => void;
  const exited = new Promise<void>((resolve) => {
    exit = resolve;
  });
  const owned = {
    child,
    exited,
    output_failed: new Promise<Error>(() => {}),
    output_error: null,
    stop: vi.fn(async () => {
      child.exitCode = 0;
      exit();
      child.stdout.end();
      child.stderr.end();
    }),
  };
  return {
    child,
    owned,
    finish() {
      child.exitCode = 0;
      exit();
    },
  };
}
async function start() {
  const version = fake();
  const wire = fake();
  vi.mocked(spawn_codex_process)
    .mockImplementationOnce(async (_argv, options) => {
      expect(options.environment.HOME).toBe(process.env.HOME);
      expect(options.close_stdin).toBe(true);
      setImmediate(() => {
        version.child.stdout.end('codex-cli 0.166.0\n');
        version.finish();
      });
      return version.owned as unknown as OwnedCodexProcess;
    })
    .mockResolvedValueOnce(wire.owned as unknown as OwnedCodexProcess);
  const notifications: unknown[] = [];
  const warnings: string[] = [];
  const ended: string[] = [];
  const client = new CodexAppServerClient({
    child_processes: new ChildProcesses(),
    description: 'synthetic',
    warning: (message) => warnings.push(message),
    handler: {
      async on_notification(method, value) {
        notifications.push([method, value]);
      },
      async on_server_request() {},
      async on_child_ended() {
        ended.push('ended');
      },
    },
  });
  await client.start({
    argv: ['/synthetic/codex', 'app-server'],
    environment: {},
    working_directory: '/tmp',
  });
  return { client, wire, notifications, warnings, ended };
}

test('raw result retains nested numeric tokens and escaped top-level names', () => {
  expect(
    raw_json_result('{"id":1,"result":{"text":"} , result","used":25.0,"nested":[1e0]}}'),
  ).toBe('{"text":"} , result","used":25.0,"nested":[1e0]}');
  expect(raw_json_result('{"result":1,"re\\u0073ult":2.0,"id":1}')).toBe('2.0');
});

test('malformed consumed notification drops while a valid delta and raw receipt still pass', async () => {
  const { client, wire, notifications, warnings } = await start();
  try {
    const pending = client.request_raw('account/rateLimits/read', {});
    await new Promise<void>((resolve) => setImmediate(resolve));
    wire.child.stdout.write(
      '{"method":"item/agentMessage/delta","params":{"threadId":"t","turnId":"u","itemId":"m"}}\n',
    );
    wire.child.stdout.write(
      '{"method":"item/agentMessage/delta","params":{"threadId":"t","turnId":"u","itemId":"m","delta":"ok"}}\n',
    );
    wire.child.stdout.write('{"id":1,"result":{"rateLimits":{"usedPercent":25.0}}}\n');
    expect(await pending).toBe('{"rateLimits":{"usedPercent":25.0}}');
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain('notification did not decode');
    expect(notifications).toHaveLength(1);
  } finally {
    await client.stop();
  }
});

test('write error rejects pending and suspended writes immediately and leaves later writes certainly unwritten', async () => {
  const { client, wire } = await start();
  try {
    const first = await client.begin_request('first');
    vi.spyOn(wire.child.stdin, 'write').mockImplementation(() => true);
    const suspended = client.notify('suspended');
    await new Promise<void>((resolve) => setImmediate(resolve));
    const first_failure = expect(first.promise).rejects.toMatchObject({
      request_may_have_been_written: true,
    });
    const suspended_failure = expect(suspended).rejects.toBeInstanceOf(CodexWireFailed);
    wire.child.stdin.emit('error', new Error('broken stdin'));
    await first_failure;
    await suspended_failure;
    await expect(client.notify('later')).rejects.toMatchObject({
      request_may_have_been_written: false,
    });
  } finally {
    await client.stop();
  }
});

test('a retired watcher and queued notification cannot reach a restarted wire', async () => {
  const { client, wire, notifications, ended } = await start();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let cleanup_count = 0;
  wire.owned.stop.mockImplementation(async () => {
    wire.finish();
    wire.child.stdout.end();
    wire.child.stderr.end();
    cleanup_count += 1;
    if (cleanup_count > 1) await gate;
  });
  // This notification waits behind a handler that stop invalidates before dispatch.
  wire.child.stdout.write(
    '{"method":"item/agentMessage/delta","params":{"threadId":"old","turnId":"old","itemId":"m","delta":"stale"}}\n',
  );
  await client.stop();
  const version = fake();
  const next = fake();
  vi.mocked(spawn_codex_process)
    .mockImplementationOnce(async () => {
      setImmediate(() => {
        version.child.stdout.end('codex-cli 0.166.0\n');
        version.finish();
      });
      return version.owned as unknown as OwnedCodexProcess;
    })
    .mockResolvedValueOnce(next.owned as unknown as OwnedCodexProcess);
  try {
    await client.start({
      argv: ['/synthetic/codex', 'app-server'],
      environment: {},
      working_directory: '/tmp',
    });
    release();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(ended).toEqual([]);
    expect(notifications).toEqual([]);
    const response = client.request('fresh');
    await new Promise<void>((resolve) => setImmediate(resolve));
    next.child.stdout.write('{"id":1,"result":{"ok":true}}\n');
    expect(await response).toEqual({ ok: true });
    expect(client.is_alive).toBe(true);
  } finally {
    release();
    await client.stop();
  }
});

test('shutdown joins a pending version probe and prevents a later app-server launch', async () => {
  const version = fake();
  vi.mocked(spawn_codex_process).mockResolvedValueOnce(
    version.owned as unknown as OwnedCodexProcess,
  );
  const client = new CodexAppServerClient({
    child_processes: new ChildProcesses(),
    description: 'pending-version',
    handler: {
      async on_notification() {},
      async on_server_request() {},
      async on_child_ended() {},
    },
  });
  const spawn_count = vi.mocked(spawn_codex_process).mock.calls.length;
  const starting = client.start({
    argv: ['/synthetic/codex', 'app-server'],
    environment: {},
    working_directory: '/tmp',
  });
  const refused = expect(starting).rejects.toBeInstanceOf(Error);
  await new Promise<void>((resolve) => setImmediate(resolve));
  await client.stop();
  await refused;
  expect(version.owned.stop).toHaveBeenCalled();
  expect(vi.mocked(spawn_codex_process).mock.calls.length - spawn_count).toBe(1);
  expect(client.is_alive).toBe(false);
});

test('an already aborted request writes nothing and preserves its child', async () => {
  const { client, wire } = await start();
  const write = vi.spyOn(wire.child.stdin, 'write');
  const abort = new AbortController();
  const reason = new Error('canceled before write');
  abort.abort(reason);
  try {
    await expect(client.request_raw('usage', {}, { signal: abort.signal })).rejects.toBe(reason);
    expect(write).not.toHaveBeenCalled();
    expect(wire.owned.stop).not.toHaveBeenCalled();
    expect(client.is_alive).toBe(true);
  } finally {
    await client.stop();
  }
});

test('abort releases a blocked write without canceling a queued independent request', async () => {
  const { client, wire } = await start();
  const write = vi.spyOn(wire.child.stdin, 'write').mockImplementationOnce(() => true);
  const abort = new AbortController();
  const blocked = client.request('blocked', {}, { signal: abort.signal });
  const reason = new Error('canceled blocked write');
  const refused = expect(blocked).rejects.toBe(reason);
  try {
    await new Promise<void>((resolve) => setImmediate(resolve));
    const queued = client.request_raw('queued');
    abort.abort(reason);
    await refused;
    await new Promise<void>((resolve) => setImmediate(resolve));
    wire.child.stdout.write('{"id":2,"result":{"ok":true}}\n');
    expect(await queued).toBe('{"ok":true}');
    expect(write).toHaveBeenCalledTimes(2);
    expect(wire.owned.stop).not.toHaveBeenCalled();
    expect(client.is_alive).toBe(true);
  } finally {
    await client.stop();
  }
});

test('deadline poisons future writes while a previously written request can still receive its answer', async () => {
  const { client, wire } = await start();
  try {
    const independent = await client.begin_request('independent');
    vi.spyOn(wire.child.stdin, 'write').mockImplementationOnce(() => true);
    await expect(
      client.request_raw('blocked', {}, { timeout_seconds: 0.01 }),
    ).rejects.toMatchObject({
      request_may_have_been_written: true,
      message: 'blocked was not answered',
    });
    wire.child.stdout.write('{"id":1,"result":{"survived":true}}\n');
    expect(await client.finish_request('independent', independent)).toEqual({ survived: true });
    await expect(client.notify('future')).rejects.toMatchObject({
      request_may_have_been_written: false,
    });
    expect(wire.owned.stop).not.toHaveBeenCalled();
    expect(wire.child.exitCode).toBeNull();
  } finally {
    await client.stop();
  }
});

test('a natural exit permits a new start without an explicit stop', async () => {
  const { client, wire, ended } = await start();
  wire.child.stdout.end();
  wire.child.stderr.end();
  wire.finish();
  await new Promise<void>((resolve) => setImmediate(resolve));
  const version = fake();
  const next = fake();
  vi.mocked(spawn_codex_process)
    .mockImplementationOnce(async () => {
      setImmediate(() => {
        version.child.stdout.end('codex-cli 0.166.0\n');
        version.finish();
      });
      return version.owned as unknown as OwnedCodexProcess;
    })
    .mockResolvedValueOnce(next.owned as unknown as OwnedCodexProcess);
  try {
    await client.start({
      argv: ['/synthetic/codex', 'app-server'],
      environment: {},
      working_directory: '/tmp',
    });
    expect(ended).toEqual(['ended']);
    const answer = client.request('restarted');
    await new Promise<void>((resolve) => setImmediate(resolve));
    next.child.stdout.write('{"id":1,"result":{"restarted":true}}\n');
    expect(await answer).toEqual({ restarted: true });
  } finally {
    await client.stop();
  }
});
