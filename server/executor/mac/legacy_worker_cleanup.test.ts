/** Legacy migration borrows synthetic capabilities and never starts or signals a real process. */
import { afterEach, expect, test, vi } from 'vitest';
import { join } from 'node:path';
import { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';
import { Deferred } from '../../conversation/runtime/state.ts';
import { BackendCommands } from '../../conversation/snapshot/commands.ts';
import * as environment from '../../conversation/snapshot/environment.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { legacy_notice } from './cleanup_reconciliation.ts';
import { legacy_process_inspector } from './platform.ts';
import type { AgentProcessSnapshot } from './platform.ts';
import { LEGACY_CLEANUP_RECEIPT, migrate_once } from './legacy_worker_cleanup.ts';
const runtimes: WorkerSupervisionRuntime[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(runtimes.splice(0).map((runtime) => runtime.close()));
});
function fixture() {
  const run = '/synthetic/run',
    legacy = join(run, 'agent-processes.json');
  const receipt = join(run, LEGACY_CLEANUP_RECEIPT);
  const contents = new Map([
    [
      legacy,
      JSON.stringify({
        processes: [
          { process_id: 11, started: 'old' },
          { process_id: 12, started: 'previous' },
        ],
      }),
    ],
  ]);
  let table: AgentProcessSnapshot[] = [
    { process_id: 11, parent_process_id: 1, started: 'old' },
    { process_id: 12, parent_process_id: 1, started: 'reused' },
    { process_id: 21, parent_process_id: 11, started: 'tool' },
  ];
  const unexpected = (): never => {
    throw new Error('No modern process authority');
  };
  const runtime = new WorkerSupervisionRuntime({
    primitives: {
      owns_isolated_session_members: false,
      requires_containment: false,
      host_boot: unexpected,
      snapshot: unexpected,
      is_absent: unexpected,
      signal: unexpected,
      prepare_launch: unexpected,
    },
  });
  runtimes.push(runtime);
  const dependencies = {
    runtime,
    warn: vi.fn(),
    files: {
      read_runtime_record: vi.fn(async (path: string) => contents.get(path) ?? null),
      atomic_runtime_record: vi.fn(async (path: string, value: string) => {
        contents.set(path, value);
      }),
      remove_runtime_record: vi.fn(async (path: string) => {
        contents.delete(path);
      }),
    },
    platform: {
      snapshot: vi.fn(async () => table),
      signal: vi.fn(async (identity: { process_id: number }, _signum: 'SIGTERM' | 'SIGKILL') => {
        expect(JSON.parse(contents.get(receipt)!)).toMatchObject({ version: 1, attempted: true });
        table = table.filter((row) => row.process_id !== identity.process_id);
      }),
    },
    inspect_legacy: vi.fn(async (identity: { process_id: number; started: string }) =>
      table.some(
        (row) => row.process_id === identity.process_id && row.started === identity.started,
      ),
    ),
  };
  return { run, legacy, receipt, contents, dependencies };
}

test('the durable attempt settles before exact root and descendant signals', async () => {
  const f = fixture(),
    committed = new Deferred<void>(),
    entered = new Deferred<void>();
  f.dependencies.files.atomic_runtime_record.mockImplementationOnce(async (path, value) => {
    entered.resolve();
    await committed.promise;
    f.contents.set(path, value);
  });
  const task = migrate_once(f.run, f.dependencies);
  try {
    await entered.promise;
    expect(f.dependencies.platform.snapshot).not.toHaveBeenCalled();
    expect(f.dependencies.platform.signal).not.toHaveBeenCalled();
    committed.resolve();
    await task;
    expect(
      f.dependencies.platform.signal.mock.calls.map(([identity, signum]) => [
        identity.process_id,
        signum,
      ]),
    ).toEqual([
      [11, 'SIGTERM'],
      [21, 'SIGTERM'],
    ]);
    expect(JSON.parse(f.contents.get(f.receipt)!)).toMatchObject({
      attempted: true,
      error: null,
      targets: [
        { process_id: 11, started: 'old' },
        { process_id: 12, started: 'previous' },
        { process_id: 21, started: 'tool' },
      ],
    });
  } finally {
    committed.resolve();
    await task;
  }
});

test('an unreadable exact identity leaves an attempted error receipt and only an observation can clear its notice', async () => {
  const f = fixture();
  f.dependencies.inspect_legacy.mockRejectedValue(new Error('Unreadable legacy birth'));
  await migrate_once(f.run, f.dependencies);
  expect(f.dependencies.platform.signal).not.toHaveBeenCalled();
  expect(JSON.parse(f.contents.get(f.receipt)!)).toMatchObject({
    attempted: true,
    error: 'Unreadable legacy birth',
  });
  await migrate_once(f.run, f.dependencies);
  expect(f.dependencies.platform.snapshot).toHaveBeenCalledOnce();
  expect(await legacy_notice(f.run, { ...f.dependencies, async *read_commands() {} })).toContain(
    '11, 12, 21',
  );
  f.dependencies.inspect_legacy.mockResolvedValue(false);
  expect(await legacy_notice(f.run, { ...f.dependencies, async *read_commands() {} })).toBeNull();
});

test('the legacy inspector distinguishes a reused PID from unreadable ps birth output', async () => {
  const original = Object.getOwnPropertyDescriptor(process, 'platform')!;
  Object.defineProperty(process, 'platform', { value: 'darwin' });
  try {
    vi.spyOn(environment, 'backend_io_is_test').mockReturnValue(false);
    const run = vi.spyOn(BackendCommands.prototype, 'run');
    const inspect = legacy_process_inspector({} as OwnedOneShotChildren);
    const identity = { process_id: 11, started: 'old' };
    run.mockResolvedValue({ exit_code: 0, standard_output: 'S old', standard_error: '' });
    expect(await inspect(identity, performance.now() / 1000 + 1)).toBe(true);
    run.mockResolvedValue({ exit_code: 0, standard_output: 'S reused', standard_error: '' });
    expect(await inspect(identity, performance.now() / 1000 + 1)).toBe(false);
    run.mockResolvedValue({ exit_code: 0, standard_output: 'S', standard_error: '' });
    await expect(inspect(identity, performance.now() / 1000 + 1)).rejects.toThrow(
      'unreadable birth',
    );
  } finally {
    Object.defineProperty(process, 'platform', original);
  }
});
