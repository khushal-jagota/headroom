import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, expect, test, vi } from 'vitest';
import { ConversationAccess } from '../../contracts.ts';
import type { ResolvedConversationStart } from '../../contracts.ts';
import { spawn_codex_process } from '../codex_app_server/process.ts';
import type { OwnedCodexProcess } from '../codex_app_server/process.ts';
import { WorkerSupervisor } from '../../../worker_supervision/index.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import { WorkerScratch } from '../../worker_scratch.ts';
import { HermesAcpLauncher } from './client.ts';

vi.mock('../../../worker_supervision/index.ts', () => ({ WorkerSupervisor: { create: vi.fn() } }));
vi.mock('../../worker_scratch.ts', () => ({ WorkerScratch: { create: vi.fn() } }));
vi.mock('../codex_app_server/process.ts', async (original) => ({
  ...(await original<typeof import('../codex_app_server/process.ts')>()),
  spawn_codex_process: vi.fn(),
}));
vi.mock('./configuration.ts', async (original) => ({
  ...(await original<typeof import('./configuration.ts')>()),
  CHILD_OUTPUT_LINE_LIMIT_BYTES: 8,
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
  const stop = vi.fn(async () => {
    child.exitCode = 0;
    exit();
    child.stdin.destroy();
    child.stdout.destroy();
    child.stderr.destroy();
  });
  return { child, exited, stop, output_failed: new Promise<Error>(() => {}), output_error: null };
}
const resolved = {
  access: ConversationAccess.full,
  role_materials: null,
  workspace_folder: '/synthetic/workspace',
} as ResolvedConversationStart;
const launch = {
  argv: ['/synthetic/python', '/synthetic/extension.py'],
  environment_overrides: [],
};

test('shutdown during spawn joins that child and permanently seals later starts', async () => {
  const owned = fake();
  let release!: (value: OwnedCodexProcess) => void;
  vi.mocked(spawn_codex_process).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const launcher = new HermesAcpLauncher(new ChildProcesses());
  const errors: unknown[] = [];
  owned.stop.mockRejectedValueOnce(new Error('synthetic cleanup failure'));
  const started = launcher.start(launch, resolved, { log: (error) => errors.push(error) });
  const rejected = expect(started).rejects.toThrow('shut down');
  const stopping = launcher.stop();
  release(owned as unknown as OwnedCodexProcess);
  await stopping;
  await rejected;
  expect(errors).toHaveLength(1);
  expect(owned.stop).toHaveBeenCalled();
  const count = vi.mocked(spawn_codex_process).mock.calls.length;
  await expect(launcher.start(launch, resolved)).rejects.toThrow('shut down');
  expect(vi.mocked(spawn_codex_process).mock.calls.length).toBe(count);
});

test('stdin error settles a write whose callback never returns', async () => {
  const owned = fake();
  vi.mocked(spawn_codex_process).mockResolvedValueOnce(owned as unknown as OwnedCodexProcess);
  vi.spyOn(owned.child.stdin, 'write').mockImplementation(() => true);
  const launcher = new HermesAcpLauncher(new ChildProcesses());
  try {
    const client = await launcher.start(launch, resolved);
    const request = client.begin_request('pending-write', {});
    const written = expect(request.written).rejects.toThrow('synthetic stdin error');
    const response = expect(request.response).rejects.toThrow('synthetic stdin error');
    await new Promise<void>((resolve) => setImmediate(resolve));
    owned.child.stdin.emit('error', new Error('synthetic stdin error'));
    await Promise.all([written, response]);
  } finally {
    await launcher.stop();
  }
});

test('an uncertain Worker stop retains scratch and retries custody cleanup while later starts stay sealed', async () => {
  const owned = fake();
  Object.assign(owned.child, { pid: 4321 });
  const identity = {
    pid: 4321,
    birth: 'birth',
    boot: 'boot',
    unit: null,
    supervision_record: '/synthetic/supervision.json',
  };
  const order: string[] = [];
  const supervisor = {
    environment: { PLAN_WORKER_PROCESS_ROOT: '/synthetic/records' },
    path: '/synthetic/supervision.json',
    argv: (argv: readonly string[]) => ['/synthetic/bootstrap', ...argv],
    spawned: vi.fn(async () => {
      order.push('spawned');
    }),
    started: vi.fn(async () => {
      order.push('started');
    }),
    identity: vi.fn(async () => identity),
    shutdown_settled: Promise.resolve(),
    stop: vi.fn(async (native_stop: () => Promise<void>, _options?: { deadline: number }) => {
      await native_stop();
      return { complete: true, reason: 'gone', scope: 'ticket' };
    }),
  };
  supervisor.stop.mockResolvedValueOnce({
    complete: false,
    reason: 'descendant unknown',
    scope: 'ticket',
  });
  const scratch = { environment: { TMPDIR: '/synthetic/scratch' }, close: vi.fn(async () => {}) };
  vi.mocked(WorkerSupervisor.create).mockResolvedValueOnce(supervisor as never);
  vi.mocked(WorkerScratch.create).mockResolvedValueOnce(scratch as never);
  vi.mocked(spawn_codex_process).mockImplementationOnce(async (argv, options) => {
    expect(order).toEqual(['record']);
    expect(argv).toEqual(['/synthetic/bootstrap', ...launch.argv]);
    expect(options.environment.TMPDIR).toBe('/synthetic/scratch');
    options.on_created?.(owned as unknown as OwnedCodexProcess);
    return owned as unknown as OwnedCodexProcess;
  });
  const errors: unknown[] = [];
  const launcher = new HermesAcpLauncher(new ChildProcesses(), {} as WorkerSupervisionRuntime, {
    report: async () => {},
    record_local_child_supervision: async () => {
      order.push('record');
    },
    record_local_child_custody: async (value) => {
      expect(value).toEqual(identity);
      order.push('custody');
    },
  });
  await launcher.start(
    launch,
    {
      ...resolved,
      role_materials: {
        role_text: 'Synthetic Worker',
        identity_environment_variables: [['PLAN_TICKET_ID', 'synthetic-worker']],
      },
    } as ResolvedConversationStart,
    { log: (error) => errors.push(error) },
  );
  expect(order).toEqual(['record', 'spawned', 'started', 'custody']);
  expect(await launcher.local_child_identity()).toEqual(identity);
  const deadline = performance.now() / 1000 + 2;
  await expect(launcher.stop({ deadline })).rejects.toThrow('the Hermes process did not stop');
  expect(supervisor.stop.mock.calls[0][1]).toEqual({ deadline });
  expect(scratch.close).not.toHaveBeenCalled();
  expect(owned.stop).not.toHaveBeenCalled();
  await expect(launcher.start(launch, resolved)).rejects.toThrow('shut down');
  const actual = Promise.withResolvers<void>();
  supervisor.shutdown_settled = actual.promise;
  await launcher.stop({ deadline });
  let actually_settled = false;
  const joined = launcher.settled().then(() => {
    actually_settled = true;
  });
  await Promise.resolve();
  expect(actually_settled).toBe(false);
  actual.resolve();
  await joined;
  expect(owned.stop).toHaveBeenCalledTimes(1);
  expect(owned.stop).toHaveBeenCalledWith({ deadline });
  expect(scratch.close).toHaveBeenCalledTimes(1);
  expect(errors).toHaveLength(1);
});

test('a failed stderr reader retains complete lines and logs once while cleanup completes', async () => {
  const owned = fake();
  vi.mocked(spawn_codex_process).mockResolvedValueOnce(owned as unknown as OwnedCodexProcess);
  const errors: unknown[] = [];
  const launcher = new HermesAcpLauncher(new ChildProcesses());
  const client = await launcher.start(launch, resolved, { log: (error) => errors.push(error) });
  owned.child.stderr.write('ok\n');
  owned.child.stderr.write('123456789');
  expect(launcher.standard_error_tail()).toBe('ok\n');
  expect(client.signal.aborted).toBe(false);
  await launcher.stop();
  await launcher.stop();
  expect(owned.stop).toHaveBeenCalledTimes(1);
  expect(errors).toHaveLength(1);
});
