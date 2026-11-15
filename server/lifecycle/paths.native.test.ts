/** Hosted Darwin proof: real APP group custody, short socket exchange and restart. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { run_server_supervisor } from './supervisor.ts';
import { request_control } from './control.ts';
import { darwin_control_socket_path } from './logic/paths.ts';
import { darwin_worker_process_info } from '../executor/mac/platform.ts';
import { observe_promise } from '../core/promise_observer.ts';

test.skipIf(process.env.GITHUB_ACTIONS !== 'true' || process.platform !== 'darwin')(
  'Darwin APP retains supervisor group through restart and releases its short control socket',
  async () => {
    const started = performance.now();
    // A short private temp root models the native runtime root without shared account state.
    const root = mkdtempSync('/var/tmp/p7-');
    const app = join(root, 'app.mjs');
    const record = join(root, 'pid');
    writeFileSync(
      app,
      `import{writeFileSync}from'node:fs';writeFileSync(${JSON.stringify(record)},String(process.pid));setInterval(()=>{},1000);process.on('SIGTERM',()=>process.exit(0));`,
    );
    const path = darwin_control_socket_path(
      join(tmpdir(), 'long source '.repeat(15), 'server.sock'),
      root,
    );
    const before = new Set(process.listeners('SIGTERM'));
    const running = run_server_supervisor({
      mode: { kind: 'foreground' },
      application: {
        node_executable: process.execPath,
        entrypoint: app,
        launch_root: root,
        environment: {},
      },
      control_socket_path: path,
      lease_path: join(root, 'lease'),
      port: 0,
    });
    let failure: unknown;
    void running.catch((error) => {
      failure = error;
    });
    const wait_pid = async (previous = 0): Promise<number> => {
      const timeout = AbortSignal.timeout(5000);
      for (;;) {
        timeout.throwIfAborted();
        if (failure) throw failure;
        let pid = 0;
        try {
          pid = Number(readFileSync(record, 'utf8'));
        } catch {}
        if (pid && pid !== previous) return pid;
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
    };
    try {
      const first = await wait_pid();
      const group = darwin_worker_process_info(process.pid)!.group;
      expect(darwin_worker_process_info(first)).toMatchObject({ parent: process.pid, group });
      expect(await request_control(path, { version: 1, operation: 'restart' })).toEqual({
        version: 1,
        status: 'accepted',
      });
      const replacement = await wait_pid(first);
      expect(darwin_worker_process_info(first)).toBeNull();
      expect(darwin_worker_process_info(replacement)).toMatchObject({ parent: process.pid, group });
    } finally {
      // Invoke only this supervisor's retained handler. No test-runner or account signal.
      const stop = process.listeners('SIGTERM').find((callback) => !before.has(callback));
      stop?.('SIGTERM');
      expect((await observe_promise(running, AbortSignal.timeout(5000))).completed).toBe(true);
      rmSync(root, { recursive: true, force: true });
      console.info(
        `Darwin home custody/socket/restart: ${Math.round(performance.now() - started)}ms`,
      );
    }
  },
  15_000,
);
