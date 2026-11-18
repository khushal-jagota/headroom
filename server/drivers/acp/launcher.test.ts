import { ChildProcesses, OneShotChildren } from '../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ConversationAccess } from '../../conversation/contracts.ts';
import type { ResolvedConversationStart } from '../../conversation/contracts.ts';
import { spawn_owned_process } from '../../lifecycle/owned_processes.ts';
import type { OwnedProcess } from '../../lifecycle/owned_processes.ts';
import { WorkerSupervisor } from '../../worker_supervision/index.ts';
import { WorkerSupervisor as RealWorkerSupervisor } from '../../worker_supervision/service.ts';
import * as records from '../../worker_supervision/records.ts';
import { uncontained_worker_supervision } from '../testing/uncontained_supervision.ts';
import { worker_supervision_fixture } from '../../conversation/backends/testing/worker_supervision_fixture.ts';
import { WorkerScratch } from '../../conversation/worker_scratch.ts';
import { AcpLauncher } from './client.ts';
import { acp_child_environment } from './environment.ts';

vi.mock('../../worker_supervision/index.ts', () => ({ WorkerSupervisor: { create: vi.fn() } }));
vi.mock('../../conversation/worker_scratch.ts', () => ({ WorkerScratch: { create: vi.fn() } }));
vi.mock('../../lifecycle/owned_processes.ts', async (original) => ({
  ...(await original<typeof import('../../lifecycle/owned_processes.ts')>()),
  spawn_owned_process: vi.fn(),
}));
vi.mock('./configuration.ts', async (original) => ({
  ...(await original<typeof import('./configuration.ts')>()),
  CHILD_OUTPUT_LINE_LIMIT_BYTES: 8,
}));
// Every launch is supervised. Tests of launcher mechanics take a synthetic supervisor.
beforeEach(() => {
  vi.mocked(WorkerSupervisor.create).mockImplementation(
    async () => worker_supervision_fixture().supervisor as never,
  );
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});
const { runtime: synthetic_runtime } = worker_supervision_fixture();
function fake() {
  const child = Object.assign(new EventEmitter(), {
    pid: 4321,
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
  environment: {},
};
const label = 'Synthetic';

test('shutdown during spawn joins that child and permanently seals later starts', async () => {
  const owned = fake();
  const spawning = Promise.withResolvers<(value: OwnedProcess) => void>();
  vi.mocked(spawn_owned_process).mockImplementationOnce(
    () => new Promise((resolve) => spawning.resolve(resolve)),
  );
  const launcher = new AcpLauncher(new ChildProcesses(), synthetic_runtime, { label });
  const errors: unknown[] = [];
  owned.stop.mockRejectedValueOnce(new Error('synthetic cleanup failure'));
  const started = launcher.start(launch, resolved, { log: (error) => errors.push(error) });
  const rejected = expect(started).rejects.toThrow('shut down');
  const release = await spawning.promise;
  const stopping = launcher.stop();
  release(owned as unknown as OwnedProcess);
  await stopping;
  await rejected;
  expect(errors).toHaveLength(1);
  expect(owned.stop).toHaveBeenCalled();
  const count = vi.mocked(spawn_owned_process).mock.calls.length;
  await expect(launcher.start(launch, resolved)).rejects.toThrow('shut down');
  expect(vi.mocked(spawn_owned_process).mock.calls.length).toBe(count);
});

test('stdin error settles a write whose callback never returns', async () => {
  const owned = fake();
  vi.mocked(spawn_owned_process).mockResolvedValueOnce(owned as unknown as OwnedProcess);
  vi.spyOn(owned.child.stdin, 'write').mockImplementation(() => true);
  const launcher = new AcpLauncher(new ChildProcesses(), synthetic_runtime, { label });
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
  const { identity, order, supervisor, scratch, runtime } = worker_supervision_fixture();
  supervisor.stop.mockResolvedValueOnce({
    complete: false,
    reason: 'descendant unknown',
    scope: 'ticket',
  });
  vi.mocked(WorkerSupervisor.create).mockResolvedValueOnce(supervisor as never);
  vi.mocked(WorkerScratch.create).mockResolvedValueOnce(scratch as never);
  vi.mocked(spawn_owned_process).mockImplementationOnce(async (argv, options) => {
    expect(order).toEqual(['record']);
    expect(argv).toEqual(['/synthetic/bootstrap', ...launch.argv]);
    expect(options.environment.TMPDIR).toBe('/synthetic/scratch');
    options.on_created?.(owned as unknown as OwnedProcess);
    return owned as unknown as OwnedProcess;
  });
  const errors: unknown[] = [];
  const launcher = new AcpLauncher(new ChildProcesses(), runtime, {
    label,
    custody_sink: {
      report: async () => {},
      record_local_child_supervision: async () => {
        order.push('record');
      },
      record_local_child_custody: async (value) => {
        expect(value).toEqual(identity);
        order.push('custody');
      },
    },
  });
  await launcher.start(
    launch,
    {
      ...resolved,
      role_materials: {
        role_text: 'Synthetic Worker',
        identity_environment_variables: [
          ['PLAN_ACTOR', 'worker'],
          ['PLAN_TICKET_ID', 'synthetic-worker'],
        ],
      },
    } as ResolvedConversationStart,
    { log: (error) => errors.push(error) },
  );
  expect(order).toEqual(['record', 'spawned', 'started', 'custody']);
  expect(await launcher.local_child_identity()).toEqual(identity);
  const deadline = performance.now() / 1000 + 2;
  await expect(launcher.stop({ deadline })).rejects.toThrow('the Synthetic process did not stop');
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
  vi.mocked(spawn_owned_process).mockResolvedValueOnce(owned as unknown as OwnedProcess);
  const errors: unknown[] = [];
  const launcher = new AcpLauncher(new ChildProcesses(), synthetic_runtime, { label });
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

// Risk: a non-Worker launch escapes custody or takes the Worker settings. A real record and a
// real supervised child prove what the Chief's custody row will name.
test('a Chief launch carrying a Ticket is supervised by its conversation, without Worker settings', async () => {
  const folder = await mkdtemp(join(tmpdir(), 'acp-chief-'));
  const child_processes = new ChildProcesses();
  const runtime = uncontained_worker_supervision(new OneShotChildren(child_processes));
  const real_spawn = (
    await vi.importActual<typeof import('../../lifecycle/owned_processes.ts')>(
      '../../lifecycle/owned_processes.ts',
    )
  ).spawn_owned_process;
  const order: string[] = [];
  vi.mocked(WorkerSupervisor.create).mockImplementationOnce((...input) =>
    RealWorkerSupervisor.create(...input),
  );
  vi.mocked(spawn_owned_process).mockImplementationOnce((...input) => {
    order.push('spawn');
    return real_spawn(...input);
  });
  let record_path = '';
  const launcher = new AcpLauncher(child_processes, runtime, {
    label,
    custody_sink: {
      report: async () => {},
      record_local_child_supervision: async (path) => {
        order.push('record');
        record_path = path;
        expect(await records.read(path)).toMatchObject({
          ticket_id: '',
          conversation_id: 'chief-conversation',
          worker_priority: false,
        });
      },
      record_local_child_custody: async (identity) => {
        order.push('custody');
        expect(identity.supervision_record).toBe(record_path);
      },
    },
  });
  const chief = {
    ...resolved,
    conversation_id: 'chief-conversation',
    workspace_folder: folder,
    role_materials: {
      role_text: 'Synthetic Chief',
      identity_environment_variables: [
        ['PLAN_ACTOR', 'chief'],
        ['PLAN_TICKET_ID', 't_belongs_to_a_worker'],
      ],
    },
  } as ResolvedConversationStart;
  try {
    await launcher.start(
      {
        argv: [process.execPath, '-e', 'process.stdin.resume()'],
        environment: acp_child_environment(
          [['PLAN_WORKER_PROCESS_ROOT', join(folder, 'records')]],
          chief,
        ),
      },
      chief,
    );
    expect(order).toEqual(['record', 'spawn', 'custody']);
    expect(WorkerScratch.create).not.toHaveBeenCalled();
  } finally {
    await launcher.stop();
    await runtime.close();
    await rm(folder, { recursive: true, force: true });
  }
});
