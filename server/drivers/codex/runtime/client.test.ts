import { ChildProcesses, OneShotChildren } from '../../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import { afterEach, expect, test, vi } from 'vitest';
import { CodexAppServerClient, CodexWireFailed } from './client.ts';
import { stringify_numeric_json } from '../../../core/http/numeric_json.ts';
import { WorkerSupervisor } from '../../../worker_supervision/service.ts';
import { WorkerScratch } from '../../../conversation/worker_scratch.ts';
import { worker_supervision_fixture } from '../../../conversation/backends/testing/worker_supervision_fixture.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import { spawn_owned_process } from '../../../lifecycle/owned_processes.ts';
import type { OwnedProcess } from '../../../lifecycle/owned_processes.ts';
import { ensure_cli_compatible } from '../../../conversation/backends/cli_compatibility.ts';
import * as records from '../../../worker_supervision/records.ts';
import { uncontained_worker_supervision } from '../../testing/uncontained_supervision.ts';

vi.mock('../../../lifecycle/owned_processes.ts', async (original) => ({
  ...(await original<typeof import('../../../lifecycle/owned_processes.ts')>()),
  spawn_owned_process: vi.fn(),
}));
vi.mock('../../../conversation/backends/cli_compatibility.ts', async (original) => ({
  ...(await original<typeof import('../../../conversation/backends/cli_compatibility.ts')>()),
  ensure_cli_compatible: vi.fn(async () => {}),
}));
afterEach(() => vi.restoreAllMocks());

function fake() {
  const child = Object.assign(new EventEmitter(), {
    pid: 12345,
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
function client_fixture(worker_supervision?: WorkerSupervisionRuntime, wire = fake()) {
  const wire_spawned = vi.fn();
  vi.mocked(spawn_owned_process).mockImplementationOnce(async (_argv, options) => {
    wire_spawned();
    options.on_created?.(wire.owned as unknown as OwnedProcess);
    return wire.owned as unknown as OwnedProcess;
  });
  const notifications: unknown[] = [];
  const warnings: string[] = [];
  const ended: string[] = [];
  const client = new CodexAppServerClient({
    child_processes: new ChildProcesses(),
    worker_supervision,
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
  const launch_options = {
    argv: ['/synthetic/codex', 'app-server'],
    environment: { PLAN_TICKET_ID: 'inherited-only' },
    working_directory: '/tmp',
  };
  return { client, wire, notifications, warnings, ended, launch_options, wire_spawned };
}
async function start() {
  const fixture = client_fixture();
  const worker_create = vi.spyOn(WorkerSupervisor, 'create');
  await fixture.client.start(fixture.launch_options);
  expect(worker_create).not.toHaveBeenCalled();
  return fixture;
}

test('malformed consumed notification drops while a valid delta and reply still pass', async () => {
  const { client, wire, notifications, warnings } = await start();
  try {
    const pending = client.request('account/rateLimits/read', {});
    await new Promise<void>((resolve) => setImmediate(resolve));
    wire.child.stdout.write(
      '{"method":"item/agentMessage/delta","params":{"threadId":"t","turnId":"u","itemId":"m"}}\n',
    );
    wire.child.stdout.write(
      '{"method":"item/agentMessage/delta","params":{"threadId":"t","turnId":"u","itemId":"m","delta":"ok"}}\n',
    );
    wire.child.stdout.write('{"id":1,"result":{"rateLimits":{"usedPercent":25.0}}}\n');
    expect(JSON.parse(stringify_numeric_json(await pending))).toEqual({
      rateLimits: { usedPercent: 25 },
    });
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
  const next = fake();
  vi.mocked(spawn_owned_process).mockResolvedValueOnce(next.owned as unknown as OwnedProcess);
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
  let probe_signal: AbortSignal | undefined;
  vi.mocked(ensure_cli_compatible).mockImplementationOnce(
    (_backend, _executable, options) =>
      new Promise((_resolve, reject) => {
        probe_signal = options!.signal!;
        // A killed probe reads no version, which the real check reports as unreadable.
        probe_signal.addEventListener('abort', () =>
          reject(new Error('Cannot read the Codex version at /synthetic/codex.')),
        );
      }),
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
  const spawn_count = vi.mocked(spawn_owned_process).mock.calls.length;
  const starting = client.start({
    argv: ['/synthetic/codex', 'app-server'],
    environment: {},
    working_directory: '/tmp',
  });
  const refused = expect(starting).rejects.toThrow('the codex child stopped during startup');
  await new Promise<void>((resolve) => setImmediate(resolve));
  await client.stop();
  await refused;
  expect(probe_signal?.aborted).toBe(true);
  expect(vi.mocked(spawn_owned_process).mock.calls.length - spawn_count).toBe(0);
  expect(client.is_alive).toBe(false);
});

test('an initialize reply below the floor refuses the running Codex child', async () => {
  const { client, wire } = await start();
  try {
    const old = client.request('initialize', {});
    await new Promise<void>((resolve) => setImmediate(resolve));
    wire.child.stdout.write(
      '{"id":1,"result":{"userAgent":"panels/0.100.0 (Debian 13.0.0; x86_64) unknown"}}\n',
    );
    await expect(old).rejects.toThrow(
      'Codex 0.100.0 at /synthetic/codex is too old. Update Codex to at least 0.148.0. Panels requires thread-aware app/read.',
    );
    const current = client.request('initialize', {});
    await new Promise<void>((resolve) => setImmediate(resolve));
    wire.child.stdout.write('{"id":2,"result":{"userAgent":"panels/0.167.2 (Linux)"}}\n');
    expect(await current).toEqual({ userAgent: 'panels/0.167.2 (Linux)' });
  } finally {
    await client.stop();
  }
});

test('an already aborted request writes nothing and preserves its child', async () => {
  const { client, wire } = await start();
  const write = vi.spyOn(wire.child.stdin, 'write');
  const abort = new AbortController();
  const reason = new Error('canceled before write');
  abort.abort(reason);
  try {
    await expect(client.request('usage', {}, { signal: abort.signal })).rejects.toBe(reason);
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
    const queued = client.request('queued');
    abort.abort(reason);
    await refused;
    await new Promise<void>((resolve) => setImmediate(resolve));
    wire.child.stdout.write('{"id":2,"result":{"ok":true}}\n');
    expect(await queued).toEqual({ ok: true });
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
    await expect(client.request('blocked', {}, { timeout_seconds: 0.01 })).rejects.toMatchObject({
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
  const next = fake();
  vi.mocked(spawn_owned_process).mockResolvedValueOnce(next.owned as unknown as OwnedProcess);
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

async function supervised_client() {
  vi.mocked(spawn_owned_process).mockReset();
  const wire = fake();
  const { identity, order, result, supervisor, scratch, runtime } = worker_supervision_fixture(
    wire.child.pid,
  );
  result.complete = false;
  vi.spyOn(WorkerSupervisor, 'create').mockResolvedValue(supervisor as unknown as WorkerSupervisor);
  vi.spyOn(WorkerScratch, 'create').mockResolvedValue(scratch as unknown as WorkerScratch);
  const { client, launch_options, wire_spawned } = client_fixture(runtime, wire);
  wire_spawned.mockImplementation(() => {
    order.push('spawn');
  });
  const options = {
    ...launch_options,
    environment: {},
    working_directory: '/private',
    conversation_id: 'synthetic-conversation',
    worker_ticket_id: 't_synthetic',
    supervision_reporter: vi.fn(async () => {
      order.push('report');
    }),
    custody_reporter: vi.fn(async () => {
      order.push('custody');
    }),
  };
  return {
    client,
    options,
    supervisor,
    scratch,
    wire,
    order,
    identity,
    prove: () => {
      result.complete = true;
    },
  };
}

test('explicit Worker admission reports custody before success and retains incomplete cleanup through retry', async () => {
  const f = await supervised_client();
  const removal = Promise.withResolvers<void>();
  const native_settled = Promise.withResolvers<void>();
  const metadata_entered = Promise.withResolvers<void>();
  const metadata_released = Promise.withResolvers<void>();
  f.supervisor.shutdown_settled = native_settled.promise;
  const deadline = performance.now() / 1000 + 5;
  try {
    await f.client.start(f.options);
    expect(f.order).toEqual(['report', 'spawn', 'spawned', 'started', 'custody']);
    expect(await f.client.local_child_identity()).toEqual(f.identity);
    expect(f.options.custody_reporter).toHaveBeenCalledWith(f.identity);
    await expect(f.client.stop({ deadline })).rejects.toThrow('cleanup remains unproven');
    expect(f.supervisor.stop.mock.calls[0][1]).toEqual({ deadline });
    let physically_settled = false;
    const settling = f.client.settled().then(() => {
      physically_settled = true;
    });
    await Promise.resolve();
    expect(physically_settled).toBe(false);
    native_settled.resolve();
    await Promise.resolve();
    expect(physically_settled).toBe(false);
    expect(f.scratch.close).not.toHaveBeenCalled();
    await expect(f.client.start(f.options)).rejects.toThrow('cleanup handles');
    f.prove();
    // Native exit can precede both pipe EOF and the watcher's captured cleanup.
    f.wire.owned.stop.mockImplementation(async () => {
      f.wire.finish();
    });
    const entered = Promise.withResolvers<void>();
    f.scratch.close.mockImplementation(async () => {
      entered.resolve();
      await removal.promise;
    });
    let stopped = false;
    const stopping = f.client.stop({ deadline }).then(() => {
      stopped = true;
    });
    await entered.promise;
    expect(stopped).toBe(false);
    await expect(f.client.start(f.options)).rejects.toThrow('cleanup handles');
    removal.resolve();
    await stopping;
    expect(f.wire.owned.stop).toHaveBeenCalledWith({ deadline });
    expect(physically_settled).toBe(false);
    f.supervisor.stop.mockImplementationOnce(async () => {
      metadata_entered.resolve();
      await metadata_released.promise;
      return { complete: true, reason: 'complete', scope: 'synthetic' };
    });
    f.wire.child.stdout.end();
    f.wire.child.stderr.end();
    await metadata_entered.promise;
    expect(physically_settled).toBe(false);
    metadata_released.resolve();
    await settling;
    expect(physically_settled).toBe(true);
  } finally {
    native_settled.resolve();
    removal.resolve();
    metadata_released.resolve();
    f.wire.child.stdout.end();
    f.wire.child.stderr.end();
    f.prove();
    await f.client.stop();
    await f.client.settled();
  }
});

test('failed Worker registration retains its native handle and scratch until cleanup is proven', async () => {
  const f = await supervised_client();
  f.supervisor.started.mockRejectedValueOnce(new Error('registration refused'));
  try {
    await expect(f.client.start(f.options)).rejects.toThrow('cleanup remains unproven');
    expect(f.scratch.close).not.toHaveBeenCalled();
    expect(f.options.custody_reporter).not.toHaveBeenCalled();
    expect(await f.client.local_child_identity()).toEqual(f.identity);
  } finally {
    f.prove();
    await f.client.stop();
  }
});

test('a conversation launch without supervision never starts even a version probe', async () => {
  vi.mocked(spawn_owned_process).mockReset();
  vi.mocked(ensure_cli_compatible).mockClear();
  const client = new CodexAppServerClient({
    child_processes: new ChildProcesses(),
    description: 'missing-runtime',
    handler: {
      async on_notification() {},
      async on_server_request() {},
      async on_child_ended() {},
    },
  });
  await expect(
    client.start({
      argv: ['/synthetic/codex'],
      environment: {},
      working_directory: '/private',
      conversation_id: 'synthetic-conversation',
    }),
  ).rejects.toThrow('supervision is unavailable');
  expect(ensure_cli_compatible).not.toHaveBeenCalled();
  expect(spawn_owned_process).not.toHaveBeenCalled();
});

// Risk (review B1): a Mac runner sink has only `report`, and its launch must still start.
test('a supervised launch whose sink has no reporters still starts under supervision', async () => {
  const f = await supervised_client();
  const { supervision_reporter: _record, custody_reporter: _custody, ...options } = f.options;
  try {
    await f.client.start(options);
    expect(f.order).toEqual(['spawn', 'spawned', 'started']);
    expect(spawn_owned_process).toHaveBeenCalledWith(
      ['/synthetic/bootstrap', '/synthetic/codex', 'app-server'],
      expect.anything(),
    );
    expect(await f.client.local_child_identity()).toEqual(f.identity);
  } finally {
    f.prove();
    await f.client.stop();
  }
});

// Risk: a non-Worker launch escapes custody or takes the Worker settings. A real record and a
// real supervised child prove what the Chief's custody row will name.
test('a Chief launch is supervised by its conversation, never by an inherited Ticket', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'codex-chief-'));
  const child_processes = new ChildProcesses();
  const runtime = uncontained_worker_supervision(new OneShotChildren(child_processes));
  const real_spawn = (
    await vi.importActual<typeof import('../../../lifecycle/owned_processes.ts')>(
      '../../../lifecycle/owned_processes.ts',
    )
  ).spawn_owned_process;
  const scratch = vi.spyOn(WorkerScratch, 'create');
  const order: string[] = [];
  let record_path = '';
  vi.mocked(spawn_owned_process).mockReset();
  vi.mocked(spawn_owned_process).mockImplementationOnce((...input) => {
    order.push('spawn');
    return real_spawn(...input);
  });
  const client = new CodexAppServerClient({
    child_processes,
    worker_supervision: runtime,
    description: 'chief',
    handler: {
      async on_notification() {},
      async on_server_request() {},
      async on_child_ended() {},
    },
  });
  try {
    await client.start({
      argv: [process.execPath, '-e', 'process.stdin.resume()'],
      environment: {
        PLAN_ACTOR: 'chief',
        PLAN_TICKET_ID: 'inherited-worker',
        PLAN_WORKER_PROCESS_ROOT: join(folder, 'records'),
      },
      working_directory: folder,
      conversation_id: 'chief-conversation',
      worker_ticket_id: null,
      supervision_reporter: async (path) => {
        order.push('record');
        record_path = path;
        expect(await records.read(path)).toMatchObject({
          ticket_id: '',
          conversation_id: 'chief-conversation',
          worker_priority: false,
        });
      },
      custody_reporter: async (identity) => {
        order.push('custody');
        expect(identity.supervision_record).toBe(record_path);
      },
    });
    expect(order).toEqual(['record', 'spawn', 'custody']);
    expect(scratch).not.toHaveBeenCalled();
  } finally {
    await client.stop();
    await runtime.close();
    await rm(folder, { recursive: true, force: true });
  }
});
