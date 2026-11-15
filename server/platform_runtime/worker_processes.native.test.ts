/** CI-only ABI proof: fake bindings cannot prove actual libproc/pidfd signatures. */
import { expect, test } from 'vitest';
import { ChildProcesses, OneShotChildren, child_lifetime } from '../lifecycle/owned_processes.ts';
import { monotonic_seconds } from '../lifecycle/configuration.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { LinuxWorkerProcesses } from './linux/worker_processes.ts';
import { DarwinWorkerProcesses } from './darwin/worker_processes.ts';

const admitted =
  process.env.GITHUB_ACTIONS === 'true' &&
  ['linux', 'darwin'].includes(process.platform) &&
  ['x64', 'arm64'].includes(process.arch);

test.skipIf(!admitted)(
  'native Worker identity signals only its newly owned exact process incarnation',
  async () => {
    const began = performance.now();
    const processes = new ChildProcesses();
    const owner = new OneShotChildren(processes);
    const primitives =
      process.platform === 'darwin'
        ? new DarwinWorkerProcesses({ children: owner })
        : new LinuxWorkerProcesses();
    let stderr = '';
    try {
      const deadline = monotonic_seconds() + 15;
      const host = await primitives.host_boot(deadline);
      expect(host.host).toMatch(new RegExp(`^${process.platform}:`));
      expect(host.boot.length).toBeGreaterThan(0);
      const child = owner.spawn(
        process.execPath,
        ['-e', "process.stdout.write('worker-native-ready\\n'); setInterval(() => {}, 1000);"],
        { stdio: ['pipe', 'pipe', 'pipe'], detached: true },
      );
      const ready = Promise.withResolvers<void>();
      let stdout = '';
      child.stdout.on('data', (chunk: Buffer) => {
        stdout += chunk.toString('utf8');
        if (stdout.includes('worker-native-ready\n')) ready.resolve();
      });
      child.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString('utf8');
      });
      child.once('error', ready.reject);
      child.once('exit', () =>
        ready.reject(new Error(`Owned native probe exited before readiness: ${stderr}`)),
      );
      expect(
        (await observe_promise(ready.promise, AbortSignal.timeout(10_000))).completed,
        stderr,
      ).toBe(true);
      expect(child.pid).toBeTypeOf('number');
      const pid = child.pid!;
      const identity = (await primitives.snapshot(deadline)).get(pid);
      if (!identity) throw new Error('Native inventory omitted the newly owned probe process');
      expect(identity.parent).toBe(process.pid);
      expect(identity.zombie).toBe(false);
      expect(identity.birth).toMatch(process.platform === 'darwin' ? /^\d+:\d+$/ : /^\d+$/);
      // Both calls refer only to this invocation's child. The wrong birth must never receive a signal.
      await primitives.signal({ ...identity, birth: identity.birth + '-different' }, 15, deadline);
      expect(await primitives.is_absent(identity, deadline)).toBe(false);
      expect(child.exitCode).toBeNull();
      expect(child.signalCode).toBeNull();
      expect(await primitives.signal(identity, 15, deadline)).toBe(true);
      expect(
        (await observe_promise(child_lifetime(child).closed, AbortSignal.timeout(5_000))).completed,
        stderr,
      ).toBe(true);
      expect(await primitives.is_absent(identity, deadline)).toBe(true);
    } finally {
      owner.seal();
      processes.seal();
      expect(
        (await observe_promise(owner.settled(), AbortSignal.timeout(5_000))).completed,
        stderr,
      ).toBe(true);
      expect(
        (await observe_promise(processes.settled(), AbortSignal.timeout(5_000))).completed,
        stderr,
      ).toBe(true);
      console.info(
        `Worker native ABI ${process.platform}/${process.arch}: ${Math.round(performance.now() - began)}ms`,
      );
    }
  },
  30_000,
);
