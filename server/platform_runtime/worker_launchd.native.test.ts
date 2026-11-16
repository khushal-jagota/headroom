/** Hosted Darwin proof: service-manager ownership cannot be established by a fake launchctl. */
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { expect, test, vi } from 'vitest';
import {
  ChildProcesses,
  OneShotChildren,
  child_lifetime,
  signal_child,
} from '../lifecycle/owned_processes.ts';
import { create_worker_supervision_runtime } from '../worker_supervision/composition.ts';
import { WorkerSupervisor, reconcile } from '../worker_supervision/index.ts';
import { RECORD_ROOT_ENV } from '../worker_supervision/config.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { BackendCommands } from '../conversation/snapshot/commands.ts';
import {
  darwin_worker_process_birth,
  darwin_worker_process_info,
} from '../executor/mac/platform.ts';
import { DarwinWorkerProcesses } from './darwin/worker_processes.ts';
import { WORKER_SWEEP_LIMIT_NOTICE } from './darwin/launchd/configuration.ts';
import type { WorkerProcess } from '../worker_supervision/contracts.ts';

const admitted = process.env.GITHUB_ACTIONS === 'true' && process.platform === 'darwin';
const owner_module = new URL('../lifecycle/owned_processes.ts', import.meta.url).href;
const composition_module = new URL('../worker_supervision/composition.ts', import.meta.url).href;
const supervision_module = new URL('../worker_supervision/index.ts', import.meta.url).href;

function capture(child: ChildProcessWithoutNullStreams) {
  const output = { stdout: '', stderr: '' };
  child.stdout.on('data', (chunk: Buffer) => (output.stdout += chunk.toString('utf8')));
  child.stderr.on('data', (chunk: Buffer) => (output.stderr += chunk.toString('utf8')));
  return output;
}

const provider_source = `
import { ChildProcesses } from ${JSON.stringify(owner_module)};
const children = new ChildProcesses();
const command = 'setTimeout(() => process.exit(0), 600000)';
const ordinary = children.spawn(process.execPath, ['-e', command], {stdio:'ignore', detached:false});
const detached = children.spawn(process.execPath, ['-e', command], {stdio:['ignore','inherit','inherit'], detached:true});
const cleared_environment = {...process.env};
delete cleared_environment.PANELS_WORKER_JOB;
const unmarked = process.env.PLAN_NATIVE_UNMARKED === '1'
  ? children.spawn(process.execPath, ['-e', command], {env:cleared_environment,stdio:['ignore','inherit','inherit'],detached:true})
  : null;
await Promise.all([ordinary,detached,...(unmarked ? [unmarked] : [])].map(child => new Promise((resolve,reject) => {
  child.once('spawn',resolve); child.once('error',reject);
})));
process.stdout.write(JSON.stringify({ordinary:ordinary.pid, detached:detached.pid, ...(unmarked ? {unmarked:unmarked.pid} : {}), marker:process.env.PANELS_WORKER_JOB})+'\\n');
process.stderr.write('separate-stderr\\n');
process.stdin.setEncoding('utf8');
process.stdin.on('data', value => {
  if(value.trim()==='crash') process.kill(process.pid,'SIGKILL');
  else process.stdout.write('echo:'+value);
});
process.stdin.on('end',()=>{
  const final = Buffer.alloc(2 * 1024 * 1024, 120);
  process.stdout.write(final,()=>{
    process.stderr.write('bulk-done\\n',()=>process.exit(0));
  });
});
setTimeout(()=>process.exit(0),600000);
`;

test.skipIf(!admitted)(
  'launchd controls liveness and the marker sweep cleans detached tools across stop and recovery',
  async () => {
    const began = performance.now();
    const root = await mkdtemp(join(tmpdir(), 'worker-launchd-native-'));
    const processes = new ChildProcesses();
    const probes = new OneShotChildren(processes);
    const runtime = create_worker_supervision_runtime({ one_shot_children: probes });
    const supervisors: WorkerSupervisor[] = [];
    const proxies: ChildProcessWithoutNullStreams[] = [];
    const observed_descendants: WorkerProcess[] = [];
    let unmarked_descendant: WorkerProcess | undefined;
    const warning = vi.spyOn(console, 'warn');
    const runs: Record<string, unknown>[] = [];
    const failed_descendants: { phase: string; pids: number[] }[] = [];
    const record_end = async (phase: string, pids: number[]) => {
      const identities = observed_descendants.filter((value) => pids.includes(value.pid));
      try {
        await expect
          .poll(
            async () =>
              Promise.all(
                identities.map((value) => runtime.primitives.is_absent(value, runtime.clock() + 5)),
              ),
            { timeout: 3_000 },
          )
          .toEqual(pids.map(() => true));
      } catch {
        failed_descendants.push({ phase, pids });
      }
    };
    const records_root = join(root, 'records');
    await mkdir(records_root, { mode: 0o700 });
    const provider = join(root, 'provider.mjs');
    await writeFile(provider, provider_source);
    const environment = {
      PATH: process.env.PATH ?? '/usr/bin:/bin',
      HOME: root,
      PLAN_SERVER_URL: 'http://127.0.0.1:1',
      PLAN_TICKET_ID: 'native-launchd-worker',
      PLAN_CONVERSATION_ID: 'native-launchd-conversation',
      [RECORD_ROOT_ENV]: records_root,
    };
    const start = async (ticket_id = environment.PLAN_TICKET_ID, unmarked = false) => {
      const supervisor = await WorkerSupervisor.create(
        { ...environment, PLAN_TICKET_ID: ticket_id, PLAN_NATIVE_UNMARKED: unmarked ? '1' : '0' },
        root,
        { runtime },
      );
      if (!supervisor) throw new Error('Synthetic Worker requires supervision');
      supervisors.push(supervisor);
      const argv = supervisor.argv([process.execPath, provider]);
      const proxy = processes.spawn(argv[0]!, argv.slice(1), {
        env: supervisor.environment,
        cwd: root,
        stdio: ['pipe', 'pipe', 'pipe'],
        detached: true,
      });
      proxies.push(proxy);
      const output = capture(proxy);
      await expect.poll(() => proxy.pid, { timeout: 5_000 }).toBeTypeOf('number');
      await supervisor.spawned(proxy.pid!);
      await supervisor.started(proxy.pid!);
      await expect.poll(() => output.stdout, { timeout: 10_000 }).toContain('\n');
      const descendants = JSON.parse(output.stdout.split('\n')[0]!) as {
        ordinary: number;
        detached: number;
        unmarked?: number;
        marker: string;
      };
      const table = await runtime.primitives.snapshot(runtime.clock() + 5);
      for (const pid of [descendants.ordinary, descendants.detached]) {
        const identity = table.get(pid);
        if (!identity) throw new Error(`Native inventory omitted owned descendant ${pid}`);
        observed_descendants.push(identity);
      }
      if (descendants.unmarked !== undefined) {
        const observed = table.get(descendants.unmarked);
        if (!observed) throw new Error('Native inventory omitted the unmarked fixture tool');
        unmarked_descendant = observed;
      }
      const identity = await supervisor.identity();
      expect(identity?.unit?.endsWith('/' + descendants.marker)).toBe(true);
      expect(identity?.unit).toMatch(
        new RegExp(`^gui/${process.getuid!()}/com\\.panels\\.worker\\.`),
      );
      runs.push({ path: supervisor.path, identity, descendants });
      return { supervisor, proxy, output, descendants, identity };
    };
    try {
      // Real pipes must preserve separate stderr, input, and EOF across launchd's bridge.
      const first = await start(environment.PLAN_TICKET_ID, true);
      first.proxy.stdin.write('ping\n');
      await expect.poll(() => first.output.stdout, { timeout: 5_000 }).toContain('echo:ping\n');
      await expect
        .poll(() => first.output.stderr, { timeout: 5_000 })
        .toContain('separate-stderr\n');
      // Hold the reader past the socket/pipe capacity. The provider waits for
      // its write callback before exit, as ordinary Node pipe writers require.
      first.proxy.stdout.pause();
      const before_final = first.output.stdout.length;
      first.proxy.stdin.end();
      await new Promise((resolve) => setTimeout(resolve, 200));
      expect(first.output.stderr).not.toContain('bulk-done');
      first.proxy.stdout.resume();
      expect((await observe_promise(child_lifetime(first.proxy).closed, 10_000)).completed).toBe(
        true,
      );
      expect(first.proxy.exitCode, first.output.stderr).toBe(0);
      const final = Buffer.from(first.output.stdout.slice(before_final));
      expect(final.length).toBe(2 * 1024 * 1024);
      expect(createHash('sha256').update(final).digest('hex')).toBe(
        createHash('sha256')
          .update(Buffer.alloc(2 * 1024 * 1024, 120))
          .digest('hex'),
      );
      expect(first.output.stderr).toContain('bulk-done');
      const first_stop = await first.supervisor.stop();
      expect(first_stop.complete).toBe(true);
      await record_end('stdin EOF', [first.descendants.ordinary, first.descendants.detached]);
      expect(
        [...warning.mock.calls.flatMap((args) => args.map(String)), first.output.stderr].some(
          (text) => text.includes(WORKER_SWEEP_LIMIT_NOTICE),
        ),
        'cleanup must log its marker-clearing limitation',
      ).toBe(true);
      expect(unmarked_descendant).toBeDefined();
      expect(await runtime.primitives.is_absent(unmarked_descendant!, runtime.clock() + 5)).toBe(
        false,
      );
      runs.push({ unmarked_descendant, limitation_notice: WORKER_SWEEP_LIMIT_NOTICE, first_stop });

      const second = await start();
      expect(second.identity?.unit).not.toBe(first.identity?.unit);
      expect(await runtime.primitives.is_absent(unmarked_descendant!, runtime.clock() + 5)).toBe(
        false,
      );
      runs.push({
        restart_with_unmarked_tool_alive: true,
        previous_job: first.identity?.unit,
        next_job: second.identity?.unit,
      });
      expect(JSON.parse(await readFile(first.supervisor.path, 'utf8')).state).toBe('complete');
      second.proxy.stdin.write('crash\n');
      expect((await observe_promise(child_lifetime(second.proxy).closed, 10_000)).completed).toBe(
        true,
      );
      expect(second.proxy.exitCode === 0 && second.proxy.signalCode === null).toBe(false);
      expect((await second.supervisor.stop()).complete).toBe(true);
      await record_end('Worker crash', [second.descendants.ordinary, second.descendants.detached]);

      const stopped = await start();
      expect((await stopped.supervisor.stop()).complete).toBe(true);
      expect((await observe_promise(child_lifetime(stopped.proxy).closed, 10_000)).completed).toBe(
        true,
      );

      await record_end('explicit stop', [
        stopped.descendants.ordinary,
        stopped.descendants.detached,
      ]);

      // A separate process is the executor owner. SIGKILL bypasses all its finally blocks.
      const executor_source = `
import {ChildProcesses,OneShotChildren} from ${JSON.stringify(owner_module)};
import {create_worker_supervision_runtime} from ${JSON.stringify(composition_module)};
import {WorkerSupervisor} from ${JSON.stringify(supervision_module)};
const children=new ChildProcesses(), probes=new OneShotChildren(children);
const runtime=create_worker_supervision_runtime({one_shot_children:probes});
const supervisor=await WorkerSupervisor.create(JSON.parse(process.argv[2]),process.argv[3],{runtime});
const argv=supervisor.argv([process.execPath,process.argv[4]]);
const child=children.spawn(argv[0],argv.slice(1),{env:supervisor.environment,cwd:process.argv[3],stdio:['pipe','pipe','pipe'],detached:true});
let output='';
child.stdout.on('data',chunk=>output+=chunk); child.stderr.resume();
await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject)});
await supervisor.spawned(child.pid);
await supervisor.started(child.pid);
await new Promise((resolve,reject)=>{
  if(output.includes('\\n')) return resolve();
  child.stdout.on('data',()=>{if(output.includes('\\n')) resolve()});
  child.once('error',reject);
});
process.stdout.write(JSON.stringify({path:supervisor.path,identity:await supervisor.identity(),descendants:JSON.parse(output.split('\\n')[0])})+'\\n');
setInterval(()=>{},1000);
`;
      const executor_file = join(root, 'executor.mjs');
      await writeFile(executor_file, executor_source);
      const executor = processes.spawn(
        process.execPath,
        [
          executor_file,
          JSON.stringify({ ...environment, PLAN_TICKET_ID: 'native-recovery-worker' }),
          root,
          provider,
        ],
        { env: environment, cwd: root, stdio: ['pipe', 'pipe', 'pipe'], detached: true },
      );
      proxies.push(executor);
      const output = capture(executor);
      await expect.poll(() => output.stdout, { timeout: 15_000 }).toContain('\n');
      const recovered = JSON.parse(output.stdout.split('\n')[0]!) as {
        path: string;
        identity: { unit: string };
        descendants: { ordinary: number; detached: number };
      };
      const recovered_table = await runtime.primitives.snapshot(runtime.clock() + 5);
      for (const pid of [recovered.descendants.ordinary, recovered.descendants.detached]) {
        const identity = recovered_table.get(pid);
        if (!identity) throw new Error(`Native inventory omitted recovered descendant ${pid}`);
        observed_descendants.push(identity);
      }
      signal_child(executor, 'SIGKILL', false);
      expect((await observe_promise(child_lifetime(executor).exited, 5_000)).completed).toBe(true);
      const reconciliation = await reconcile(records_root, { runtime });
      expect(reconciliation.complete, reconciliation.reason).toBe(true);
      expect(JSON.parse(await readFile(recovered.path, 'utf8')).state).toBe('complete');
      runs.push({ recovered, reconciliation });
      await record_end('executor SIGKILL and recovery', [
        recovered.descendants.ordinary,
        recovered.descendants.detached,
      ]);
      const restarted = await start('native-recovery-worker');
      expect((await restarted.supervisor.identity())?.unit).not.toBe(recovered.identity.unit);
      expect((await restarted.supervisor.stop()).complete).toBe(true);
      await record_end('restart stop', [
        restarted.descendants.ordinary,
        restarted.descendants.detached,
      ]);

      // All five marked detached tools must end. The marker-cleared fixture demonstrates the declared limit.
      expect(await runtime.primitives.is_absent(unmarked_descendant!, runtime.clock() + 5)).toBe(
        false,
      );
      const survivors: WorkerProcess[] = [];
      for (const descendant of observed_descendants)
        if (!(await runtime.primitives.is_absent(descendant, runtime.clock() + 5)))
          survivors.push(descendant);
      runs.push({ survivors, failed_descendants });
      expect(
        failed_descendants,
        'the sweep left marked descendants alive past its cleanup boundary',
      ).toEqual([]);
      expect(survivors, 'the sweep left an owned marked detached descendant alive').toEqual([]);
    } finally {
      const cleanup_errors: unknown[] = [];
      for (const supervisor of supervisors)
        try {
          const result = await supervisor.stop();
          if (!result.complete) cleanup_errors.push(result);
        } catch (error) {
          cleanup_errors.push(error);
        }
      // Native test cleanup uses only exact births observed for this fixture's own children.
      for (const descendant of observed_descendants)
        await runtime.primitives.signal(descendant, 9, runtime.clock() + 5);
      if (unmarked_descendant)
        await runtime.primitives.signal(unmarked_descendant, 9, runtime.clock() + 5);
      for (const proxy of proxies) signal_child(proxy, 'SIGKILL', false);
      await reconcile(records_root, { runtime }).catch((error) => cleanup_errors.push(error));
      await runtime.close();
      probes.seal();
      processes.seal();
      await probes.settled();
      expect((await observe_promise(processes.settled(), 10_000)).completed).toBe(true);
      warning.mockRestore();
      const evidence = {
        platform: process.platform,
        architecture: process.arch,
        domain: `gui/${process.getuid!()}`,
        milliseconds: Math.round(performance.now() - began),
        runs,
        cleanup_errors,
      };
      console.info('Worker launchd native proof', JSON.stringify(evidence));
      if (process.env.NATIVE_EVIDENCE)
        await writeFile(
          join(process.env.NATIVE_EVIDENCE, 'worker-launchd.json'),
          JSON.stringify(evidence, null, 2) + '\n',
        );
      if (cleanup_errors.length === 0) await rm(root, { recursive: true, force: true });
      expect(cleanup_errors).toEqual([]);
    }
  },
  120_000,
);

test.skipIf(!admitted)(
  'real cross-user EPERM retires a changed legacy birth without signaling the current process',
  async () => {
    const began = performance.now();
    const processes = new ChildProcesses();
    const probes = new OneShotChildren(processes);
    const commands = new BackendCommands(probes);
    const native = new DarwinWorkerProcesses({ children: probes });
    const evidence: Record<string, unknown> = {
      platform: process.platform,
      architecture: process.arch,
      caller_uid: process.getuid!(),
      foreign_pid: 1,
      signal_policy:
        'Only kill(pid, 0) existence probes. No signal or cleanup targets this system process.',
    };
    try {
      expect(process.getuid!(), 'the foreign-process proof requires a nonroot caller').not.toBe(0);
      const own_birth = darwin_worker_process_birth(process.pid);
      const own_libproc = darwin_worker_process_info(process.pid);
      expect(own_birth).toMatch(/^\d+:\d+$/);
      expect(own_birth).toBe(own_libproc?.birth);
      const own_microseconds = Number(own_birth!.split(':')[1]);
      expect(own_microseconds).toBeGreaterThanOrEqual(0);
      expect(own_microseconds).toBeLessThan(1_000_000);
      evidence.own_sysctl_birth = own_birth;
      evidence.own_libproc_birth = own_libproc?.birth;

      // PID 1 is the stable root-owned launchd process. All observations below are read-only.
      const owner = await commands.run(['/bin/ps', '-p', '1', '-o', 'uid=', '-o', 'comm='], {
        timeout_seconds: 5,
      });
      expect(owner.exit_code, owner.standard_error).toBe(0);
      const identity = /^\s*(\d+)\s+(.+?)\s*$/.exec(owner.standard_output);
      expect(identity, owner.standard_output).not.toBeNull();
      evidence.foreign_uid = Number(identity![1]);
      evidence.foreign_command = identity![2];
      expect(evidence.foreign_uid).toBe(0);
      expect(identity![2]).toMatch(/(?:^|\/)launchd$/);
      let probe_error: string | undefined;
      try {
        process.kill(1, 0);
      } catch (error) {
        probe_error = (error as NodeJS.ErrnoException).code;
      }
      evidence.existence_probe_error = probe_error;
      expect(probe_error, 'the actual foreign process must refuse the existence probe').toBe(
        'EPERM',
      );
      const birth = darwin_worker_process_birth(1);
      expect(birth).toMatch(/^\d+:\d+$/);
      evidence.current_birth = birth;
      const [seconds, microseconds] = birth!.split(':');
      const current: WorkerProcess = {
        pid: 1,
        birth: birth!,
        parent: 0,
        group: 1,
        session: 1,
        zombie: false,
      };
      const historical = { ...current, birth: `${BigInt(seconds!) - 1n}:${microseconds}` };
      evidence.historical_birth = historical.birth;
      const deadline = performance.now() / 1000 + 5;
      evidence.historical_absent = await native.is_absent(historical, deadline);
      evidence.current_absent = await native.is_absent(current, deadline);
      expect(evidence.historical_absent).toBe(true);
      expect(evidence.current_absent).toBe(false);
      const after = darwin_worker_process_birth(1);
      evidence.birth_after = after;
      expect(after).toBe(birth);
    } finally {
      probes.seal();
      processes.seal();
      await probes.settled();
      expect((await observe_promise(processes.settled(), 5_000)).completed).toBe(true);
      evidence.milliseconds = Math.round(performance.now() - began);
      console.info('Worker cross-user legacy birth proof', JSON.stringify(evidence));
      if (process.env.NATIVE_EVIDENCE)
        await writeFile(
          join(process.env.NATIVE_EVIDENCE, 'worker-legacy-foreign-process.json'),
          JSON.stringify(evidence, null, 2) + '\n',
        );
    }
  },
  25_000,
);
