/** Synthetic evidence never opens a process handle or contacts a service manager. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../lifecycle/contracts.ts';
import type { WorkerProcess, WorkerProcessPrimitives } from '../worker_supervision/contracts.ts';
import { WorkerSupervisionRuntime } from '../worker_supervision/runtime.ts';
import * as records from '../worker_supervision/records.ts';
import type { LocalChildCustodyRow } from './local_child_custody_contracts.ts';
import { LocalChildCustody } from './child_custody.ts';
import { BackendCommands } from './snapshot/commands.ts';
import * as environment from './snapshot/environment.ts';
import * as control from './worker_unit/control.ts';

const runtimes: WorkerSupervisionRuntime[] = [];
afterEach(async () => {
  await Promise.all(runtimes.splice(0).map((runtime) => runtime.close()));
  vi.restoreAllMocks();
});
const identity = { pid: 34567, birth: '123', boot: 'boot', unit: null, supervision_record: null };
const row = (extra: Partial<LocalChildCustodyRow> = {}): LocalChildCustodyRow => ({
  conversation_id: 'conversation',
  generation: 'generation',
  state: 'started',
  ...identity,
  runtime_epoch: 'epoch',
  occurrence_id: 'occurrence',
  host: 'linux:machine',
  ...extra,
});
function fixture(host = 'linux:machine', clock?: () => number) {
  let current: WorkerProcess | null = {
    pid: identity.pid,
    birth: identity.birth,
    parent: 1,
    group: 2,
    session: 3,
    zombie: false,
  };
  const native: WorkerProcessPrimitives = {
    owns_isolated_session_members: true,
    requires_containment: false,
    host_boot: vi.fn(async () => ({ host, boot: 'boot' })),
    snapshot: vi.fn(async () => new Map(current ? [[current.pid, current]] : [])),
    is_absent: vi.fn(
      async (recorded) => !current || current.birth !== recorded.birth || current.zombie,
    ),
    signal: vi.fn(async () => {
      current = null;
      return true;
    }),
    prepare_launch: vi.fn(),
  };
  const owner = {
    retain_worker_unit: vi.fn(() => ({
      spawn: vi.fn(() => {
        throw new Error('No real control');
      }),
    })),
  } as unknown as OwnedChildProcesses;
  const runtime = new WorkerSupervisionRuntime({ primitives: native, clock });
  runtimes.push(runtime);
  const custody = new LocalChildCustody({
    runtime,
    child_processes: owner,
    one_shot_children: {} as OwnedOneShotChildren,
  });
  return {
    custody,
    native,
    owner,
    runtime,
    replace: (value: WorkerProcess | null) => {
      current = value;
    },
  };
}

test('pending custody needs a same-machine ended boot and never stops during inspection', async () => {
  const f = fixture();
  expect(
    await f.custody.retirement_row_proven(row({ state: 'launch_pending', pid: null, birth: null })),
  ).toBe(false);
  expect(await f.custody.retirement_row_proven(row({ state: 'launch_pending', boot: 'old' }))).toBe(
    true,
  );
  expect(
    await f.custody.retirement_row_proven(
      row({ state: 'launch_pending', boot: 'old', host: 'another' }),
    ),
  ).toBe(false);
  expect(await f.custody.retirement_row_proven(row({ state: 'retired' }))).toBe(true);
  expect(f.native.signal).not.toHaveBeenCalled();
});

test('exact Linux custody checks the host and birth before an explicit stop', async () => {
  const f = fixture();
  expect(await f.custody.stop_recorded_child(identity, 'foreign')).toBe(false);
  expect(f.native.signal).not.toHaveBeenCalled();
  expect(await f.custody.stop_recorded_child(identity, 'linux:machine')).toBe(true);
  expect(f.native.signal).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({ pid: identity.pid, birth: identity.birth }),
    15,
    expect.any(Number),
  );
  expect(await f.custody.retirement_row_proven(row())).toBe(true);

  let clock = 0;
  const resistant = fixture('linux:machine', () => clock);
  vi.mocked(resistant.native.signal).mockResolvedValue(true);
  vi.mocked(resistant.native.is_absent)
    .mockResolvedValueOnce(false)
    .mockImplementationOnce(async () => {
      clock = 10;
      throw new Error('Transient process identity read failure');
    })
    .mockResolvedValueOnce(true);
  expect(await resistant.custody.stop_recorded_child(identity, 'linux:machine')).toBe(true);
  expect(vi.mocked(resistant.native.signal).mock.calls.map((call) => call[1])).toEqual([15, 9]);
});

test('historical Mac custody distinguishes UTC birth reuse from unreadable evidence without signals', async () => {
  const original = Object.getOwnPropertyDescriptor(process, 'platform')!;
  Object.defineProperty(process, 'platform', { value: 'darwin' });
  try {
    vi.spyOn(environment, 'backend_io_is_test').mockReturnValue(false);
    const f = fixture('darwin:machine', () => 10);
    const kill = vi.spyOn(process, 'kill').mockReturnValue(true);
    const legacy = row({ host: 'darwin:machine', birth: 'Mon Nov  2 14:00:00 2026' });
    const recorded = { ...identity, birth: legacy.birth! };
    const run = vi.spyOn(BackendCommands.prototype, 'run').mockResolvedValue({
      exit_code: 0,
      standard_output: legacy.birth!,
      standard_error: '',
    });
    const options = { deadline: 15 };
    expect(await f.custody.retirement_row_proven(legacy, options)).toBe(false);
    expect(run).toHaveBeenLastCalledWith(['/bin/ps', '-o', 'lstart=', '-p', String(identity.pid)], {
      timeout_seconds: 5,
      environment_overrides: { LC_ALL: 'C', TZ: 'UTC' },
    });
    f.replace({
      pid: identity.pid,
      birth: '123:456',
      parent: 1,
      group: 2,
      session: 3,
      zombie: true,
    });
    expect(await f.custody.stop_recorded_child(recorded, 'darwin:machine', options)).toBe(false);
    run.mockResolvedValue({
      exit_code: 0,
      standard_output: 'Tue Nov  3 14:00:00 2026',
      standard_error: '',
    });
    expect(await f.custody.retirement_row_proven(legacy, options)).toBe(true);
    expect(await f.custody.stop_recorded_child(recorded, 'darwin:machine', options)).toBe(true);
    run.mockResolvedValue({ exit_code: 0, standard_output: '', standard_error: '' });
    expect(await f.custody.retirement_row_proven(legacy, options)).toBe(false);
    run.mockRejectedValue(new Error('Unreadable process birth'));
    expect(await f.custody.retirement_row_proven(legacy, options)).toBe(false);
    kill.mockImplementation(() => {
      throw Object.assign(new Error('gone'), { code: 'ESRCH' });
    });
    expect(await f.custody.retirement_row_proven(legacy, options)).toBe(true);
    run.mockClear();
    expect(await f.custody.retirement_row_proven({ ...legacy, host: 'foreign' }, options)).toBe(
      false,
    );
    expect(await f.custody.retirement_row_proven({ ...legacy, boot: 'old' }, options)).toBe(true);
    expect(await f.custody.retirement_row_proven({ ...legacy, birth: '123:456' }, options)).toBe(
      true,
    );
    vi.spyOn(records, 'read').mockRejectedValue(new Error('Unreadable supervision journal'));
    expect(
      await f.custody.retirement_row_proven(
        { ...legacy, supervision_record: '/synthetic/journal' },
        options,
      ),
    ).toBe(false);
    expect(run).not.toHaveBeenCalled();
    expect(f.native.is_absent).toHaveBeenCalledOnce();
    expect(f.native.signal).not.toHaveBeenCalled();
    expect(kill.mock.calls.every((call) => call[1] === 0)).toBe(true);
  } finally {
    Object.defineProperty(process, 'platform', original);
  }
});

test('legacy units require complete retirement properties and retain the exact cleanup owner', async () => {
  const f = fixture();
  const unit = 'panels-worker-' + 'a'.repeat(32) + '.service';
  const legacy = { ...identity, unit };
  const run = vi.spyOn(BackendCommands.prototype, 'run').mockResolvedValue({
    exit_code: 0,
    standard_output: 'LoadState=loaded\nActiveState=active\nControlGroup=',
    standard_error: '',
  });
  f.replace(null);
  expect(await f.custody.retirement_row_proven(row({ unit }))).toBe(false);
  f.replace({
    pid: identity.pid,
    birth: identity.birth,
    parent: 1,
    group: 2,
    session: 3,
    zombie: false,
  });
  const retained = vi.spyOn(control, 'control_retained_with_timeout').mockResolvedValue([0, '']);
  retained.mockImplementation(async (_owner, action) => [
    0,
    action === 'show'
      ? 'LoadState=loaded\nActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\nControlGroup=\nSlice=panels-workers.slice\nKillMode=control-group'
      : '',
  ]);
  expect(await f.custody.stop_recorded_child(legacy, 'linux:machine')).toBe(true);
  expect(f.owner.retain_worker_unit).toHaveBeenCalledExactlyOnceWith({
    name: unit,
    finished: expect.any(Promise),
  });
  await expect(
    vi.mocked(f.owner.retain_worker_unit).mock.calls[0][0].finished,
  ).resolves.toBeUndefined();
  expect(retained.mock.calls.map((call) => call[1])).toEqual(['stop', 'reset-failed', 'show']);
  expect(run).toHaveBeenCalledTimes(1);
});

test('an expired custody deadline and failed host evidence grant no stop authority', async () => {
  const f = fixture();
  expect(await f.custody.stop_recorded_child(identity, 'linux:machine', { deadline: 0 })).toBe(
    false,
  );
  vi.mocked(f.native.host_boot).mockRejectedValue(new Error('unavailable'));
  expect(await f.custody.host_boot_identity()).toBe(null);
  expect(await f.custody.retirement_row_proven(row())).toBe(false);
  expect(f.native.signal).not.toHaveBeenCalled();
});

// An absent wrapper PID cannot prove that an unsupervised Ticket descendant ended.
// Reuse the synthetic host snapshot to distinguish root absence from an ended boot.
test('legacy Linux Ticket custody needs an ended boot after its root disappears', async () => {
  const f = fixture();
  f.replace(null);
  expect(await f.custody.retirement_row_proven(row(), undefined, true)).toBe(false);
  expect(await f.custody.retirement_row_proven(row())).toBe(true);
  expect(await f.custody.stop_recorded_child(identity, 'linux:machine', undefined, true)).toBe(
    false,
  );
  vi.mocked(f.native.host_boot).mockResolvedValue({ host: 'linux:machine', boot: 'next-boot' });
  expect(await f.custody.retirement_row_proven(row(), undefined, true)).toBe(true);
  expect(f.native.signal).not.toHaveBeenCalled();
});

// Risk: after an update's restart, a Worker that a failed stop left "not proven" stays
// fenced although its processes ended with the service. Startup and the cleanup recheck
// both read this proof. No other test takes a not_proven record with group custody to a
// proven row. Real record file, synthetic process table and process group.
test('a Worker left not proven is proven after a restart once its process group is gone', async () => {
  const root = mkdtempSync(join(tmpdir(), 'custody-restart-'));
  const path = join(root, '1'.repeat(32) + '.json');
  const cgroup = { path: '/synthetic/panels-worker', inode: 1n, root_device: 2n, root_inode: 3n };
  const root_process = {
    pid: identity.pid,
    birth: identity.birth,
    parent: 1,
    group: 2,
    session: 3,
  };
  try {
    await records.write(path, {
      version: 1,
      ticket_id: 'ticket',
      host: 'linux:machine',
      boot: 'boot',
      root: { ...root_process, zombie: false },
      processes: [{ ...root_process, zombie: false }],
      state: 'not_proven',
      reason: 'Worker stop deadline expired',
      isolated: true,
      cgroup,
      cgroup_joined: true,
    });
    const source = row({ supervision_record: path });
    const restart = (alive: boolean) => {
      const f = fixture();
      if (!alive) f.replace(null);
      Object.assign(f.native, { requires_containment: true });
      const runtime = new WorkerSupervisionRuntime({
        primitives: f.native,
        cgroups: {
          create: async () => cgroup,
          join: async () => {},
          kill: async () => {},
          empty: async () => !alive,
          remove: async () => {},
        },
      });
      runtimes.push(runtime);
      return new LocalChildCustody({
        runtime,
        child_processes: f.owner,
        one_shot_children: {} as OwnedOneShotChildren,
      });
    };
    // The same server process, with the Worker still alive: the fence holds.
    expect(await restart(true).retirement_row_proven(source, undefined, true)).toBe(false);
    expect((await records.read(path)).state).toBe('not_proven');
    // After the service stop the group is gone, and the next start proves it from fresh evidence.
    expect(await restart(false).retirement_row_proven(source, undefined, true)).toBe(true);
    expect((await records.read(path)).state).toBe('complete');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
