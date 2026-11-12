import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import { PassThrough } from 'node:stream';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { afterEach, expect, test, vi } from 'vitest';
import { OwnedCodexProcess, spawn_codex_process, within } from './process.ts';

afterEach(() => vi.restoreAllMocks());
function fake_child() {
  return Object.assign(new EventEmitter(), {
    pid: 12345,
    exitCode: null as number | null,
    signalCode: null,
    stdin: new PassThrough(),
    stdout: new PassThrough(),
    stderr: new PassThrough(),
  });
}

test('cleanup never signals an already reaped leader or its retired process group', async () => {
  const child = fake_child();
  child.exitCode = 0;
  const kill = vi.spyOn(process, 'kill').mockReturnValue(true);
  const owned = new OwnedCodexProcess(child as unknown as ChildProcessWithoutNullStreams);
  await owned.stop();
  expect(kill).not.toHaveBeenCalled();
  expect(child.stdout.destroyed).toBe(true);
});

test('a graceful exit during shutdown prevents a later SIGKILL', async () => {
  const child = fake_child();
  const owned = new OwnedCodexProcess(child as unknown as ChildProcessWithoutNullStreams);
  const kill = vi.spyOn(process, 'kill').mockImplementation(() => {
    queueMicrotask(() => {
      child.exitCode = 0;
      child.emit('exit', 0, null);
    });
    return true;
  });
  await owned.stop();
  expect(kill.mock.calls.map((call) => call[1])).toEqual(['SIGTERM']);
});

test('a later process error does not prove exit and output errors are retained safely', async () => {
  const child = fake_child();
  const owned = new OwnedCodexProcess(child as unknown as ChildProcessWithoutNullStreams);
  let exited = false;
  void owned.exited.then(() => {
    exited = true;
  });
  child.emit('error', new Error('later process error'));
  const output_error = new Error('stdout read failed');
  child.stdout.emit('error', output_error);
  child.stderr.emit('error', new Error('stderr read failed'));
  expect(await owned.output_failed).toBe(output_error);
  expect(owned.output_error).toBe(output_error);
  expect(exited).toBe(false);
  child.exitCode = 1;
  child.emit('exit', 1, null);
  await owned.exited;
  expect(exited).toBe(true);
  await owned.stop();
});

/** Signal mocks cannot prove that the OS kills descendants in the owned group. */
test.skipIf(process.platform !== 'linux')(
  'shutdown kills a live TERM-resistant leader and its descendant with retained pipes',
  async () => {
    const grandchild = `
      process.on('SIGTERM', () => {});
      setInterval(() => {}, 1000);
      process.send('ready');
    `;
    const leader = `
      const { spawn } = require('node:child_process');
      process.on('SIGTERM', () => {});
      const child = spawn(process.execPath, ['-e', ${JSON.stringify(grandchild)}], {
        stdio: ['ignore', 'inherit', 'inherit', 'ipc']
      });
      child.once('message', () => process.stdout.write(String(child.pid) + '\\n'));
      setInterval(() => {}, 1000);
    `;
    const started = performance.now();
    const owned = await spawn_codex_process([process.execPath, '-e', leader], {
      environment: Object.fromEntries(
        Object.entries(process.env).filter(
          (entry): entry is [string, string] => entry[1] !== undefined,
        ),
      ),
      working_directory: process.cwd(),
    });
    let descendant: number | null = null;
    const terminated = () => {
      if (descendant === null) return false;
      try {
        // A killed orphan can await reaping by PID 1. It is no longer a running process.
        return readFileSync(`/proc/${descendant}/stat`, 'utf8').split(') ')[1]!.startsWith('Z ');
      } catch (error) {
        const code = (error as NodeJS.ErrnoException).code;
        if (code === 'ENOENT' || code === 'ESRCH') return true;
        throw error;
      }
    };
    try {
      let output = '';
      const ready = new Promise<void>((resolve) =>
        owned.child.stdout.on('data', (chunk: Buffer) => {
          output += chunk.toString('utf8');
          if (output.includes('\n')) resolve();
        }),
      );
      expect(await within(ready, 5)).toBe(true);
      descendant = Number(output.trim());
      expect(descendant).toBeGreaterThan(0);
      expect(owned.child.exitCode).toBeNull();
      expect(process.kill(descendant, 0)).toBe(true);
      await owned.stop();
      const deadline = performance.now() + 2000;
      while (!terminated() && performance.now() < deadline)
        await new Promise<void>((resolve) => setTimeout(resolve, 10));
      expect(terminated()).toBe(true);
      expect(owned.child.signalCode).toBe('SIGKILL');
      expect(owned.child.stdout.destroyed).toBe(true);
      expect(performance.now() - started).toBeLessThan(5500);
    } finally {
      await owned.stop();
      if (descendant !== null && !terminated()) {
        try {
          process.kill(descendant, 'SIGKILL');
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
        }
      }
    }
  },
);
