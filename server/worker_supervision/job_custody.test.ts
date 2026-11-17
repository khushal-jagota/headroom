/** Job lifecycle decisions must never depend on a stale or inaccessible PID. */
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { WorkerSupervisor } from './service.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';
import { inspect_record, reconcile, stop_record } from './inspection.ts';
import * as records from './records.ts';
import { RECORD_ROOT_ENV } from './config.ts';
import type {
  WorkerJobIdentity,
  WorkerJobPrimitives,
  WorkerProcessPrimitives,
} from './contracts.ts';

const roots: string[] = [];
const runtimes: WorkerSupervisionRuntime[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(runtimes.splice(0).map((runtime) => runtime.close()));
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});
const no_process_evidence: WorkerProcessPrimitives = {
  owns_isolated_session_members: false,
  requires_containment: false,
  async host_boot() {
    return { host: 'darwin:test', boot: 'boot' };
  },
  async snapshot() {
    throw new Error('PID inventory must not decide job custody');
  },
  async is_absent() {
    throw new Error('stale PID returned EPERM');
  },
  async signal() {
    throw new Error('no PID signals are authorized');
  },
  prepare_launch() {
    throw new Error('no PID bootstrap');
  },
};
class Jobs implements WorkerJobPrimitives {
  state: 'absent' | 'loaded' | 'running' = 'absent';
  stopped = 0;
  swept = 0;
  fail_stop = false;
  identity(_root: string, token: string): WorkerJobIdentity {
    return {
      domain: `gui/${process.getuid!()}`,
      label: `com.panels.worker.${'a'.repeat(20)}.${token}`,
    };
  }
  async status() {
    return this.state;
  }
  async sweep() {
    this.swept++;
  }
  async stop() {
    this.stopped++;
    if (this.fail_stop) throw new Error('launchd denied job removal');
    this.state = 'absent';
  }
}
function runtime(jobs: Jobs) {
  const value = new WorkerSupervisionRuntime({ primitives: no_process_evidence, jobs });
  runtimes.push(value);
  return value;
}
async function setup() {
  const root = await mkdtemp(join(tmpdir(), 'job-custody-'));
  roots.push(root);
  const jobs = new Jobs();
  const owner = runtime(jobs);
  const environment = { PLAN_TICKET_ID: 'test-ticket', [RECORD_ROOT_ENV]: root };
  const supervisor = (await WorkerSupervisor.create(environment, root, { runtime: owner }))!;
  return { root, jobs, owner, environment, supervisor };
}

test('new Mac custody rejects a duplicate and retires by job label despite unavailable PID evidence', async () => {
  const { root, jobs, owner, environment, supervisor } = await setup();
  expect(await records.read(supervisor.path)).toMatchObject({
    owner: null,
    root: null,
    processes: [],
  });
  await expect(WorkerSupervisor.create(environment, root, { runtime: owner })).rejects.toThrow(
    'retained Worker job',
  );
  jobs.state = 'running';
  const record = await records.read(supervisor.path);
  record.state = 'running';
  await records.write(supervisor.path, record);
  expect((await inspect_record(supervisor.path, { runtime: owner })).complete).toBe(false);
  expect((await stop_record(supervisor.path, { runtime: owner })).complete).toBe(true);
  expect(jobs.stopped).toBe(1);
  expect((await records.read(supervisor.path)).launch_revoked).toBe(true);
  expect(owner.active_jobs.size).toBe(0);
  const natural = (await WorkerSupervisor.create(environment, root, { runtime: owner }))!;
  const natural_record = await records.read(natural.path);
  natural_record.state = 'running';
  await records.write(natural.path, natural_record);
  expect((await inspect_record(natural.path, { runtime: owner })).complete).toBe(true);
  expect(jobs.swept).toBe(1);
  expect((await records.read(natural.path)).state).toBe('complete');
});

test('a fresh executor removes loaded crash residue before a new launch and retains refusal evidence', async () => {
  const { root, jobs, supervisor } = await setup();
  jobs.state = 'loaded';
  const recovered = runtime(jobs);
  jobs.fail_stop = true;
  expect((await reconcile(root, { runtime: recovered })).complete).toBe(false);
  expect((await records.read(supervisor.path)).launch_revoked).toBe(true);
  jobs.fail_stop = false;
  const notice = vi.spyOn(console, 'warn').mockImplementation(() => {});
  const residue = await records.read(supervisor.path);
  residue.launchd_socket_directory = '/outside-the-private-transport-root';
  await records.write(supervisor.path, residue);
  expect((await reconcile(root, { runtime: recovered })).complete).toBe(true);
  const retained = await records.read(supervisor.path);
  expect(retained.state).toBe('complete');
  expect(notice).toHaveBeenCalledWith(
    expect.stringContaining('transport cleanup did not finish'),
    '/outside-the-private-transport-root',
    expect.any(Error),
  );
  delete retained.launchd_socket_directory;
  await records.write(supervisor.path, retained);
  expect((await reconcile(root, { runtime: recovered })).complete).toBe(true);
  expect(jobs.state).toBe('absent');
});

test('a changed recorded label never authorizes cleanup of another job', async () => {
  const { jobs, owner, supervisor } = await setup();
  const record = await records.read(supervisor.path);
  const label = record.launchd!.label;
  record.launchd = {
    ...record.launchd!,
    label: label.slice(0, -1) + (label.endsWith('0') ? '1' : '0'),
  };
  await records.write(supervisor.path, record);
  expect((await stop_record(supervisor.path, { runtime: owner })).complete).toBe(false);
  expect(jobs.stopped).toBe(0);
});
