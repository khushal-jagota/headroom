/** Synthetic evidence never opens a process handle or contacts a service manager. */
import { afterEach, expect, test, vi } from 'vitest';
import type { OwnedChildProcesses, OwnedOneShotChildren } from '../lifecycle/contracts.ts';
import type { WorkerProcess, WorkerProcessPrimitives } from '../worker_supervision/contracts.ts';
import { WorkerSupervisionRuntime } from '../worker_supervision/runtime.ts';
import type { LocalChildCustodyRow } from './local_child_custody_contracts.ts';
import { LocalChildCustody } from './child_custody.ts';
import { BackendCommands } from './snapshot/commands.ts';
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

test('a surviving legacy Mac timestamp stays uncertain despite a native birth mismatch', async () => {
  const f = fixture('darwin:machine');
  const kill = vi.spyOn(process, 'kill').mockReturnValue(true);
  const legacy = row({ host: 'darwin:machine', birth: 'Mon Nov  2 14:00:00 2026' });
  expect(await f.custody.retirement_row_proven(legacy)).toBe(false);
  expect(f.native.is_absent).not.toHaveBeenCalled();
  expect(
    await f.custody.stop_recorded_child({ ...identity, birth: legacy.birth! }, 'darwin:machine'),
  ).toBe(false);
  expect(f.native.signal).not.toHaveBeenCalled();
  expect(kill.mock.calls.every((call) => call[1] === 0)).toBe(true);
  kill.mockImplementation(() => {
    throw Object.assign(new Error('gone'), { code: 'ESRCH' });
  });
  expect(await f.custody.retirement_row_proven(legacy)).toBe(true);
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
