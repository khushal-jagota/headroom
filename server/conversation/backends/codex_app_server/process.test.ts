import { readFileSync } from 'node:fs';
import { expect, test } from 'vitest';
import { spawn_codex_process, within } from './process.ts';

/** The OS boundary matters: signal-call mocks cannot prove inherited-pipe cleanup. */
test.skipIf(process.platform !== 'linux')(
  'an exited leader cannot leave a TERM-resistant descendant with retained pipes',
  async () => {
    const grandchild = `
      process.on('SIGTERM', () => {});
      setInterval(() => {}, 1000);
      process.send('ready');
    `;
    const leader = `
      const { spawn } = require('node:child_process');
      const child = spawn(process.execPath, ['-e', ${JSON.stringify(grandchild)}], {
        stdio: ['ignore', 'inherit', 'inherit', 'ipc']
      });
      child.once('message', () => {
        process.stdout.write(String(child.pid) + '\\n', () => process.exit(0));
      });
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
    const output: Buffer[] = [];
    owned.child.stdout.on('data', (chunk: Buffer) => output.push(chunk));
    let descendant: number | null = null;
    try {
      expect(await within(owned.exited, 5)).toBe(true);
      descendant = Number(Buffer.concat(output).toString('utf8').trim());
      expect(descendant).toBeGreaterThan(0);
      expect(owned.child.stdout.readableEnded).toBe(false);
      expect(process.kill(descendant, 0)).toBe(true);
      await owned.stop();
      const deadline = performance.now() + 2000;
      const terminated = () => {
        try {
          // A killed orphan can await reaping by PID 1. It is no longer a running process.
          return readFileSync(`/proc/${descendant}/stat`, 'utf8').split(') ')[1]!.startsWith('Z ');
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code === 'ENOENT') return true;
          throw error;
        }
      };
      while (!terminated() && performance.now() < deadline)
        await new Promise<void>((resolve) => setTimeout(resolve, 10));
      expect(terminated()).toBe(true);
      expect(owned.child.stdout.destroyed).toBe(true);
      expect(performance.now() - started).toBeLessThan(5000);
    } finally {
      await owned.stop();
      if (descendant !== null) {
        try {
          process.kill(descendant, 'SIGKILL');
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error;
        }
      }
    }
  },
);
