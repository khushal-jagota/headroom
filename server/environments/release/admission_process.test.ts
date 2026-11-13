/** Actual private Node children only. No package/native/tool admission or host facts run. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import type { ChildProcess } from 'node:child_process';
import { expect, test, vi } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { run_runtime_json_probe, run_runtime_probe } from './admission_process.ts';
import { run_release_command } from './process.ts';

test.skipIf(process.platform !== 'linux')(
  'admission joins only its actual probe and kills its live owned group at the fixed deadline',
  async () => {
    const state = mkdtempSync(join(tmpdir(), 'panels-admission-'));
    const owner = new ChildProcesses(),
      native_spawn = owner.spawn.bind(owner),
      spawned = vi.spyOn(owner, 'spawn');
    const native_signal = process.kill.bind(process);
    const signal = vi.spyOn(process, 'kill').mockImplementation((pid, name) => {
      const child = spawned.mock.results
        .filter((result) => result.type === 'return')
        .map((result) => result.value as ChildProcess)
        .find((value) => value.pid === -pid);
      if (!child || child.exitCode !== null || child.signalCode !== null)
        throw new Error('Fixture refuses a signal outside its actual unreaped child handles');
      return native_signal(pid, name);
    });
    let pending: Promise<unknown> | undefined;
    const bounded = async (work: Promise<unknown>): Promise<unknown> => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        return await Promise.race([
          work,
          new Promise<never>((_resolve, reject) => {
            timer = setTimeout(
              () => reject(new Error('Admission fixture work did not return')),
              8000,
            );
          }),
        ]);
      } finally {
        clearTimeout(timer);
      }
    };
    try {
      vi.stubEnv('ANTHROPIC_API_KEY', 'synthetic-must-not-inherit');
      vi.stubEnv('HTTPS_PROXY', 'http://synthetic-must-not-inherit.invalid');
      const unrelated = owner.spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {
        stdio: 'ignore',
      });
      pending = once(unrelated, 'spawn');
      await bounded(pending);
      const report = { package_paths: { example: 'x'.repeat(5000) } };
      pending = run_runtime_json_probe(
        [
          process.execPath,
          '-e',
          `console.log(JSON.stringify({report:${JSON.stringify(report)},home:process.env.HOME,
        secret:process.env.ANTHROPIC_API_KEY??null,proxy:process.env.HTTPS_PROXY??null}))`,
        ],
        state,
        performance.now() / 1000 + 10,
        owner,
      );
      expect(await bounded(pending)).toEqual({ report, home: state, secret: null, proxy: null });
      expect(unrelated.exitCode).toBeNull(); // A per-probe join cannot wait for the shared owner's cohort.
      pending = (async () => {
        const deadline = performance.now() / 1000 + 10;
        const execution = {
          child_processes: owner,
          signal: new AbortController().signal,
          deadline,
        };
        expect(
          await run_release_command(
            [process.execPath, '-e', 'process.stdout.write("source-only")'],
            { timeout_seconds: null },
            execution,
          ),
        ).toEqual({ code: 0, stdout: 'source-only', stderr: '' });
        const controller = new AbortController(),
          reason = new Error('Recorded build abort');
        spawned.mockImplementationOnce((command, args, options) => {
          const child = native_spawn(command, args, options);
          // Abort before the caller receives the exact handle; its post-publish fence must reap it.
          controller.abort(reason);
          return child;
        });
        await expect(
          run_release_command(
            [process.execPath, '-e', 'setInterval(()=>{},1000)'],
            { timeout_seconds: null },
            { ...execution, signal: controller.signal },
          ),
        ).rejects.toBe(reason);
        expect((spawned.mock.results.at(-1)!.value as ChildProcess).signalCode).toBe('SIGKILL');
        expect(unrelated.exitCode).toBeNull();
        const before_deadline = spawned.mock.calls.length;
        const clock = vi.spyOn(performance, 'now').mockReturnValue((deadline + 1) * 1000);
        try {
          await expect(
            run_release_command(
              [process.execPath, '-e', 'throw Error("must not launch")'],
              { timeout_seconds: null },
              execution,
            ),
          ).rejects.toThrow('release operation exceeded its time budget');
          expect(spawned).toHaveBeenCalledTimes(before_deadline);
        } finally {
          clock.mockRestore();
        }
      })();
      await bounded(pending);
      const before = signal.mock.calls.length;
      pending = run_runtime_json_probe(
        [process.execPath, '-e', 'console.log("not JSON")'],
        state,
        performance.now() / 1000 + 10,
        owner,
      );
      await expect(bounded(pending)).rejects.toThrow('runtime probe returned no valid JSON');
      expect(signal.mock.calls).toHaveLength(before); // Report rejection after close never signals a retired group.
      // This unit owns no grandchildren. The hosted Python case independently pins/reaps those.
      pending = run_runtime_probe(
        [process.execPath, '-e', 'setInterval(()=>{},1000)'],
        state,
        performance.now() / 1000 + 5,
        owner,
      );
      void pending.catch(() => {});
      await expect(bounded(pending)).rejects.toThrow('command timed out');
      const probe = spawned.mock.results.at(-1)!.value as ChildProcess;
      expect(signal).toHaveBeenCalledWith(-probe.pid!, 'SIGKILL');
      expect(probe.signalCode).toBe('SIGKILL');
    } finally {
      owner.force_kill(performance.now() / 1000 + 5);
      await pending?.catch(() => {});
      await owner.settled();
      signal.mockRestore();
      spawned.mockRestore();
      vi.unstubAllEnvs();
      rmSync(state, { recursive: true, force: true });
    }
  },
  45_000,
); // Five independent 8s observations plus cleanup. No production deadline changes.
