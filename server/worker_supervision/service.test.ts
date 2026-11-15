/** Synthetic journals and native contracts prove custody without a service manager or cgroup host. */
import { mkdtemp, readFile, rm, chmod, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { Deferred } from '../conversation/runtime/state.ts';
import {
  WorkerSupervisor,
  WorkerSupervisionRuntime,
  inspect_record,
  stop_record,
  reconcile,
} from './index.ts';
import { prepare_launch } from './launch.ts';
import * as records from './records.ts';
import type {
  CgroupIdentity,
  WorkerCgroupPrimitives,
  WorkerProcess,
  WorkerProcessPrimitives,
  WorkerProcessRecord,
} from './contracts.ts';

const directories: string[] = [],
  runtimes: WorkerSupervisionRuntime[] = [];
afterEach(async () => {
  await Promise.all(runtimes.splice(0).map((runtime) => runtime.close()));
  await Promise.all(
    directories.splice(0).map((path) => rm(path, { recursive: true, force: true })),
  );
  vi.restoreAllMocks();
});
const process_record = (pid: number, birth = 'birth', parent = 1): WorkerProcess => ({
  pid,
  birth,
  parent,
  group: pid,
  session: pid,
  zombie: false,
});
class Native implements WorkerProcessPrimitives {
  readonly owns_isolated_session_members = false;
  requires_containment = false;
  table = new Map<number, WorkerProcess>([[process.pid, process_record(process.pid, 'owner')]]);
  signals: [number, number][] = [];
  fail = false;
  prepared = 0;
  on_signal: ((pid: number, signal: number) => void) | undefined;
  async host_boot() {
    return { host: 'host', boot: 'boot' };
  }
  async snapshot() {
    if (this.fail) throw new Error('inventory unavailable');
    return new Map(this.table);
  }
  async is_absent(recorded: WorkerProcess) {
    const current = this.table.get(recorded.pid);
    return current === undefined || current.birth !== recorded.birth || current.zombie;
  }
  async signal(recorded: WorkerProcess, signum: number) {
    this.signals.push([recorded.pid, signum]);
    this.on_signal?.(recorded.pid, signum);
    return true;
  }
  prepare_launch() {
    this.prepared += 1;
  }
}
class Cgroups implements WorkerCgroupPrimitives {
  identity: CgroupIdentity = {
    path: '/synthetic/panels-worker-' + '1'.repeat(32),
    inode: 9007199254740993n,
    root_device: 18446744073709551001n,
    root_inode: 9007199254740995n,
  };
  populated = true;
  removed = 0;
  on_join: (() => Promise<void>) | undefined;
  async create() {
    return this.identity;
  }
  async join() {
    await this.on_join?.();
  }
  async kill() {
    this.populated = false;
  }
  async empty() {
    return !this.populated;
  }
  async remove() {
    this.removed += 1;
  }
}
async function setup(options: { cgroups?: WorkerCgroupPrimitives; clock?: () => number } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'worker-custody-'));
  directories.push(root);
  const native = new Native();
  const runtime = new WorkerSupervisionRuntime({ primitives: native, ...options });
  runtimes.push(runtime);
  const path = join(root, '1'.repeat(32) + '.json');
  const record: WorkerProcessRecord = {
    version: 1,
    ticket_id: 'ticket',
    host: 'host',
    boot: 'boot',
    root: process_record(20),
    processes: [process_record(20)],
    state: 'running',
    isolated: true,
  };
  const environment = {
    PLAN_TICKET_ID: 'ticket',
    PLAN_CONVERSATION_ID: 'conversation',
    PLAN_WORKER_PROCESS_ROOT: root,
  };
  return { root, path, record, environment, native, runtime };
}

test('journals reject nonprivate files and invalid process evidence before signals', async () => {
  const f = await setup();
  await records.write(f.path, f.record);
  const bytes = await readFile(f.path, 'utf8');
  await chmod(f.path, 0o644);
  await expect(records.read(f.path)).rejects.toThrow('private and owned');
  await chmod(f.path, 0o600);
  await writeFile(f.path, bytes.replace('"pid": 20', '"pid": -20'));
  expect((await stop_record(f.path, { runtime: f.runtime })).complete).toBe(false);
  expect(f.native.signals).toEqual([]);
});

test('revocation blocks bootstrap and pending custody prevents a second launch for the same Ticket', async () => {
  const f = await setup();
  expect(await WorkerSupervisor.create({}, f.root, { runtime: f.runtime })).toBeNull();
  await expect(
    WorkerSupervisor.create({ PLAN_TICKET_ID: 'missing-root' }, f.root, { runtime: f.runtime }),
  ).rejects.toThrow('root is missing');
  const worker = (await WorkerSupervisor.create(f.environment, f.root, { runtime: f.runtime }))!;
  await expect(
    WorkerSupervisor.create(f.environment, f.root, { runtime: f.runtime }),
  ).rejects.toThrow('retained Worker process custody');
  await worker.cancel_unspawned();
  await expect(prepare_launch(worker.path, f.runtime)).rejects.toThrow(
    'revoked before backend execution',
  );
  expect(f.native.prepared).toBe(0);
  expect(await records.read(worker.path)).toMatchObject({
    state: 'complete',
    launch_revoked: true,
    root: null,
  });
});

test('bootstrap commits cgroup ownership before join and missing delegation allows execution but retains a cleanup fence', async () => {
  const cgroups = new Cgroups();
  const f = await setup({ cgroups });
  f.native.requires_containment = true;
  const worker = (await WorkerSupervisor.create(f.environment, f.root, { runtime: f.runtime }))!;
  cgroups.on_join = async () =>
    expect((await records.read(worker.path)).cgroup).toEqual(cgroups.identity);
  await prepare_launch(worker.path, f.runtime);
  expect(await worker.identity()).toMatchObject({
    pid: process.pid,
    boot: 'boot',
    supervision_record: worker.path,
  });
  expect(await records.read(worker.path)).toMatchObject({ state: 'running', cgroup_joined: true });
  f.native.table.clear();
  cgroups.populated = false;
  expect((await inspect_record(worker.path, { runtime: f.runtime })).complete).toBe(true);
  expect(cgroups.removed).toBe(1);
  const other = await setup();
  other.native.requires_containment = true;
  const uncontained = (await WorkerSupervisor.create(other.environment, other.root, {
    runtime: other.runtime,
  }))!;
  await prepare_launch(uncontained.path, other.runtime);
  expect(other.native.prepared).toBe(1);
  other.native.table.clear();
  expect(await inspect_record(uncontained.path, { runtime: other.runtime })).toMatchObject({
    complete: false,
    reason: expect.stringContaining('cgroup'),
  });
  expect((await records.read(uncontained.path)).state).toBe('running');
});

test('reused PIDs do not authorize signals and copied journals cannot certify cleanup', async () => {
  const f = await setup();
  await records.write(f.path, f.record);
  f.native.table.set(20, process_record(20, 'replacement'));
  f.native.table.set(30, process_record(30, 'unrelated', 20));
  expect((await stop_record(f.path, { runtime: f.runtime })).complete).toBe(true);
  expect(f.native.signals).toEqual([]);
  expect(f.native.table.has(30)).toBe(true);
  await records.write(f.path, { ...f.record, host: 'another-machine' });
  expect((await inspect_record(f.path, { runtime: f.runtime })).complete).toBe(false);
  expect(f.native.signals).toEqual([]);
  await records.write(f.path, {
    ...f.record,
    state: 'complete',
    root: null,
    processes: [],
    owner: process_record(10),
  });
  expect((await inspect_record(f.path, { runtime: f.runtime })).complete).toBe(false);
});

test('Linux cleanup requires an empty joined cgroup and kernel escalation leaves sibling Workers alone', async () => {
  let offset = 0;
  const cgroups = new Cgroups();
  const f = await setup({ cgroups, clock: () => performance.now() / 1000 + offset });
  f.native.requires_containment = true;
  f.native.table.set(20, process_record(20));
  f.native.table.set(30, process_record(30, 'sibling'));
  f.native.table.set(99, process_record(99, 'unobserved-detached'));
  f.native.on_signal = (pid, signum) => {
    if (signum === 15) {
      f.native.table.delete(pid);
      offset += 5.001;
    }
  };
  const kill = vi.spyOn(cgroups, 'kill').mockImplementation(async () => {
    cgroups.populated = false;
    f.native.table.delete(99);
  });
  await records.write(f.path, { ...f.record, cgroup: cgroups.identity, cgroup_joined: true });
  expect((await stop_record(f.path, { runtime: f.runtime })).complete).toBe(true);
  expect(kill).toHaveBeenCalledExactlyOnceWith(cgroups.identity);
  expect(f.native.table.has(30)).toBe(true);
  expect(f.native.table.has(99)).toBe(false);
  expect(f.native.signals).toEqual([[20, 15]]);
  await records.write(f.path, f.record);
  expect((await inspect_record(f.path, { runtime: f.runtime })).complete).toBe(false);
});

test('failed ownership observation retains prior evidence and never calls the native stopper', async () => {
  const f = await setup();
  const worker = (await WorkerSupervisor.create(f.environment, f.root, { runtime: f.runtime }))!;
  await records.write(worker.path, f.record);
  f.native.fail = true;
  const native_stop = vi.fn(async () => {});
  expect((await worker.stop(native_stop)).complete).toBe(false);
  expect(native_stop).not.toHaveBeenCalled();
  expect(await records.read(worker.path)).toMatchObject({
    state: 'not_proven',
    processes: f.record.processes,
    observation_error: expect.stringContaining('inventory unavailable'),
  });
});

test('native transport failure does not skip owned-process escalation and reconciliation isolates bad records', async () => {
  const f = await setup();
  const worker = (await WorkerSupervisor.create(f.environment, f.root, { runtime: f.runtime }))!;
  await records.write(worker.path, f.record);
  f.native.table.set(20, process_record(20));
  f.native.on_signal = (pid) => {
    f.native.table.delete(pid);
  };
  const result = await worker.stop(async () => {
    throw new Error('transport failed');
  });
  expect(result).toMatchObject({
    complete: true,
    reason: expect.stringContaining('Native Worker stop failed'),
  });
  await worker.settled();
  await records.write(f.path, { ...f.record, host: 'foreign' });
  const sibling = join(f.root, '2'.repeat(32) + '.json');
  await records.write(sibling, { ...f.record, ticket_id: 'sibling', boot: 'old-boot' });
  const reconciled = await reconcile(f.root, { runtime: f.runtime });
  expect(reconciled.complete).toBe(false);
  expect(reconciled.records.find((record) => record.record_path === sibling)?.result.complete).toBe(
    true,
  );
  expect((await records.read(sibling)).boot_ended).toBe(true);
  const pending = (await WorkerSupervisor.create(
    { ...f.environment, PLAN_TICKET_ID: 'pending-native' },
    f.root,
    { runtime: f.runtime },
  ))!;
  await records.write(pending.path, { ...f.record, ticket_id: 'pending-native' });
  f.native.table.set(20, process_record(20));
  const release = new Deferred<void>();
  try {
    const timed = await pending.stop(() => release.promise);
    expect(timed.complete).toBe(true);
    expect(timed.reason).toContain('Native Worker stop deadline expired');
    let joined = false;
    const settled = pending.settled().then(() => {
      joined = true;
    });
    await Promise.resolve();
    expect(joined).toBe(false);
    await f.runtime.close();
    expect(joined).toBe(false);
    release.resolve();
    await settled;
    expect(joined).toBe(true);
  } finally {
    release.resolve();
    await pending.settled();
  }
}, 10_000);
