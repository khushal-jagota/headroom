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
import { copy_environment } from '../core/environment.ts';
import { DarwinWorkerProcesses } from './darwin/worker_processes.ts';
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
import { writeSync } from 'node:fs';
import { ChildProcesses } from ${JSON.stringify(owner_module)};
const children = new ChildProcesses();
const command = 'setTimeout(() => process.exit(0), 600000)';
const ordinary = children.spawn(process.execPath, ['-e', command], {stdio:'ignore', detached:false});
const detached = children.spawn(process.execPath, ['-e', command], {stdio:'ignore', detached:true});
await Promise.all([ordinary,detached].map(child => new Promise((resolve,reject) => {
  child.once('spawn',resolve); child.once('error',reject);
})));
process.stdout.write(JSON.stringify({ordinary:ordinary.pid, detached:detached.pid})+'\\n');
process.stderr.write('separate-stderr\\n');
process.stdin.setEncoding('utf8');
process.stdin.on('data', value => {
  if(value.trim()==='crash') process.kill(process.pid,'SIGKILL');
  else process.stdout.write('echo:'+value);
});
process.stdin.on('end',()=>{
  const final = Buffer.alloc(2 * 1024 * 1024, 120);
  let written = 0;
  while(written < final.length) written += writeSync(1, final.subarray(written));
  writeSync(2, 'bulk-done\\n');
  process.exit(0);
});
setTimeout(()=>process.exit(0),600000);
`;

test.skipIf(!admitted)(
  'launchd owns protocol, crash, detached descendants, and recovery after executor death',
  async () => {
    const began = performance.now();
    const root = await mkdtemp(join(tmpdir(), 'worker-launchd-native-'));
    const processes = new ChildProcesses();
    const probes = new OneShotChildren(processes);
    const runtime = create_worker_supervision_runtime({ one_shot_children: probes });
    const supervisors: WorkerSupervisor[] = [];
    const proxies: ChildProcessWithoutNullStreams[] = [];
    const observed_descendants: WorkerProcess[] = [];
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
    const start = async (ticket_id = environment.PLAN_TICKET_ID) => {
      const supervisor = await WorkerSupervisor.create(
        { ...environment, PLAN_TICKET_ID: ticket_id },
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
      };
      const table = await runtime.primitives.snapshot(runtime.clock() + 5);
      for (const pid of [descendants.ordinary, descendants.detached]) {
        const identity = table.get(pid);
        if (!identity) throw new Error(`Native inventory omitted owned descendant ${pid}`);
        observed_descendants.push(identity);
      }
      const identity = await supervisor.identity();
      expect(identity?.unit).toMatch(
        new RegExp(`^gui/${process.getuid!()}/com\\.panels\\.worker\\.`),
      );
      runs.push({ path: supervisor.path, identity, descendants });
      return { supervisor, proxy, output, descendants };
    };
    try {
      // Real pipes must preserve separate stderr, input, and EOF across launchd's bridge.
      const first = await start();
      first.proxy.stdin.write('ping\n');
      await expect.poll(() => first.output.stdout, { timeout: 5_000 }).toContain('echo:ping\n');
      await expect
        .poll(() => first.output.stderr, { timeout: 5_000 })
        .toContain('separate-stderr\n');
      // Hold the reader past the socket/pipe capacity. The provider uses
      // synchronous writes, so inherited nonblocking descriptors cannot hide EAGAIN.
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
      expect((await first.supervisor.stop()).complete).toBe(true);
      await record_end('stdin EOF', Object.values(first.descendants));

      const second = await start();
      second.proxy.stdin.write('crash\n');
      expect((await observe_promise(child_lifetime(second.proxy).closed, 10_000)).completed).toBe(
        true,
      );
      expect(second.proxy.exitCode === 0 && second.proxy.signalCode === null).toBe(false);
      expect((await second.supervisor.stop()).complete).toBe(true);
      await record_end('Worker crash', Object.values(second.descendants));

      const stopped = await start();
      expect((await stopped.supervisor.stop()).complete).toBe(true);
      expect((await observe_promise(child_lifetime(stopped.proxy).closed, 10_000)).completed).toBe(
        true,
      );

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
      await record_end('executor SIGKILL and recovery', Object.values(recovered.descendants));
      const restarted = await start('native-recovery-worker');
      expect((await restarted.supervisor.identity())?.unit).not.toBe(recovered.identity.unit);
      expect((await restarted.supervisor.stop()).complete).toBe(true);
      await record_end('restart stop', Object.values(restarted.descendants));

      // A job's absence must also mean its descendants ended. Detached tools are material here.
      const survivors: WorkerProcess[] = [];
      for (const descendant of observed_descendants)
        if (!(await runtime.primitives.is_absent(descendant, runtime.clock() + 5)))
          survivors.push(descendant);
      runs.push({ survivors, failed_descendants });
      expect(
        failed_descendants,
        'launchd removal left owned descendants alive past its cleanup boundary',
      ).toEqual([]);
      expect(survivors, 'launchd removal left an owned detached descendant alive').toEqual([]);
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
      for (const proxy of proxies) signal_child(proxy, 'SIGKILL', false);
      await reconcile(records_root, { runtime }).catch((error) => cleanup_errors.push(error));
      await runtime.close();
      probes.seal();
      processes.seal();
      await probes.settled();
      expect((await observe_promise(processes.settled(), 10_000)).completed).toBe(true);
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
  'legacy EPERM with a changed birth retires only the historical incarnation',
  async () => {
    const processes = new ChildProcesses();
    const probes = new OneShotChildren(processes);
    const native = new DarwinWorkerProcesses({ children: probes });
    const child = processes.spawn(process.execPath, ['-e', 'setTimeout(()=>{},600000)'], {
      env: copy_environment(process.env),
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: true,
    });
    const signal = vi.fn();
    try {
      await expect.poll(() => child.pid, { timeout: 5_000 }).toBeTypeOf('number');
      const current = (await native.snapshot(performance.now() / 1000 + 5)).get(child.pid!);
      if (!current) throw new Error('Native inventory omitted the unrelated fixture process');
      const primitive = new DarwinWorkerProcesses({
        children: probes,
        native: {
          info: () => ({ ...current, birth: current.birth }),
          snapshot: () => new Map([[current.pid, current]]),
          exists: () => {
            throw Object.assign(new Error('synthetic other-user probe'), { code: 'EPERM' });
          },
          signal,
          prepare_session: () => {},
        },
      });
      expect(
        await primitive.is_absent(
          { ...current, birth: current.birth + '-old' },
          performance.now() / 1000 + 5,
        ),
      ).toBe(true);
      expect(signal).not.toHaveBeenCalled();
      expect(await native.is_absent(current, performance.now() / 1000 + 5)).toBe(false);
      expect(child.exitCode).toBeNull();
    } finally {
      signal_child(child, 'SIGKILL', false);
      probes.seal();
      processes.seal();
      await probes.settled();
      expect((await observe_promise(processes.settled(), 5_000)).completed).toBe(true);
    }
  },
  20_000,
);
