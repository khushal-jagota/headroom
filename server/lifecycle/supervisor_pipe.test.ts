/** Real supervisor and APP processes: a supervisor death or group hangup stops APP and frees its port. */
import { spawn } from 'node:child_process';
import {
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  readlinkSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { connect } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { expect, test } from 'vitest';

interface AppRecord {
  readonly pid: number;
  readonly port: number;
  readonly descendant: number;
  readonly pipe: string | null;
}
const alive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};
const until = async <T>(read: () => T | undefined, label: string, seconds = 10): Promise<T> => {
  const deadline = performance.now() + seconds * 1000;
  for (;;) {
    const value = read();
    if (value !== undefined) return value;
    if (performance.now() > deadline) throw new Error(`timed out waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
};
const port_refuses = (port: number): Promise<boolean> =>
  new Promise((resolve) => {
    const socket = connect(port, '127.0.0.1');
    socket.once('connect', () => {
      socket.destroy();
      resolve(false);
    });
    socket.once('error', () => resolve(true));
  });

test.each(['SIGKILL', 'SIGHUP'] as const)(
  '%s to a foreground supervisor stops APP and closes its port, despite a surviving APP child',
  async (signal) => {
    const started = performance.now();
    const root = mkdtempSync(join(tmpdir(), 'supervisor-pipe-'));
    const record = join(root, 'app.json');
    const app = join(root, 'app.mjs');
    const supervisor = join(root, 'supervisor.mjs');
    // APP leaves a detached child alive. If that child held APP's pipe end, the supervisor's
    // wait for APP's close would hang to its stop bound.
    writeFileSync(
      app,
      [
        `import { follow_supervisor_lifetime } from ${JSON.stringify(pathToFileURL(join(import.meta.dirname, 'supervisor_pipe.ts')).href)};`,
        "import { createServer } from 'node:http';",
        "import { spawn } from 'node:child_process';",
        "import { readlinkSync, renameSync, writeFileSync } from 'node:fs';",
        'follow_supervisor_lifetime();',
        "const descendant = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 60000)'], { stdio: 'ignore', detached: true });",
        'descendant.unref();',
        "process.on('SIGTERM', () => { console.error('APP received SIGTERM'); server.close(() => process.exit(0)); });",
        "const server = createServer().listen(0, '127.0.0.1', () => {",
        "  const pipe = process.platform === 'linux' ? readlinkSync('/proc/self/fd/4') : null;",
        `  writeFileSync(${JSON.stringify(record + '.tmp')}, JSON.stringify({ pid: process.pid, port: server.address().port, descendant: descendant.pid, pipe }));`,
        `  renameSync(${JSON.stringify(record + '.tmp')}, ${JSON.stringify(record)});`,
        '});',
      ].join('\n'),
    );
    writeFileSync(
      supervisor,
      [
        `import { run_server_supervisor } from ${JSON.stringify(pathToFileURL(join(import.meta.dirname, 'supervisor.ts')).href)};`,
        'process.exitCode = await run_server_supervisor({',
        "  mode: { kind: 'foreground' },",
        `  application: { node_executable: process.execPath, entrypoint: ${JSON.stringify(app)}, launch_root: ${JSON.stringify(root)}, environment: {} },`,
        `  control_socket_path: ${JSON.stringify(join(root, 'control.sock'))},`,
        `  lease_path: ${JSON.stringify(join(root, 'lease'))},`,
        '  port: 0,',
        '});',
      ].join('\n'),
    );
    let output = '';
    // Its own process group models a terminal's foreground group.
    const host = spawn(process.execPath, [supervisor], {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: true,
    });
    host.stdout.on('data', (chunk: Buffer) => (output += chunk.toString()));
    host.stderr.on('data', (chunk: Buffer) => (output += chunk.toString()));
    const exited = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>((resolve) =>
      host.once('close', (code, reason) => resolve({ code, signal: reason })),
    );
    let descendant = 0;
    try {
      const value = await until(
        () =>
          existsSync(record) ? (JSON.parse(readFileSync(record, 'utf8')) as AppRecord) : undefined,
        `APP record; output: ${output}`,
      );
      descendant = value.descendant;
      if (value.pipe !== null) {
        // Linux names the pipe end: APP's child must hold no copy of it.
        expect(value.pipe).toMatch(/^socket:\[\d+\]$/);
        const held = readdirSync(`/proc/${descendant}/fd`).map((fd) => {
          try {
            return readlinkSync(`/proc/${descendant}/fd/${fd}`);
          } catch {
            return '';
          }
        });
        expect(held).not.toContain(value.pipe);
      }
      expect(await port_refuses(value.port)).toBe(false);
      // A terminal hangup reaches the whole group. On Darwin that group includes APP.
      process.kill(signal === 'SIGHUP' ? -host.pid! : host.pid!, signal);
      const result = await exited;
      if (signal === 'SIGKILL') expect(result.signal).toBe('SIGKILL');
      else expect(result).toEqual({ code: 0, signal: null });
      await until(() => (alive(value.pid) ? undefined : true), `APP exit; output: ${output}`);
      expect(await port_refuses(value.port)).toBe(true);
      expect(alive(descendant)).toBe(true);
      expect(output).toContain('APP received SIGTERM');
      if (signal === 'SIGKILL')
        expect(output).toContain('Panels supervisor is gone; the application stops.');
      else expect(output).not.toContain('Panels supervisor is gone');
    } finally {
      if (alive(host.pid!)) host.kill('SIGKILL');
      if (descendant && alive(descendant)) process.kill(descendant, 'SIGKILL');
      rmSync(root, { recursive: true, force: true });
      console.info(`${signal} supervisor pipe case: ${Math.round(performance.now() - started)}ms`);
    }
  },
  20_000,
);
