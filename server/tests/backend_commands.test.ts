/** A real scripted process group proves cleanup that recording commands cannot show. */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { BackendCommands } from '../conversation/snapshot/commands.ts';

interface OwnedProcesses {
  parent: number;
  descendant: number;
}
const pause = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
async function within<T>(operation: Promise<T>, milliseconds: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new Error('Scripted command did not settle in time')),
          milliseconds,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
function stopped(pid: number): boolean {
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
    // Zombies and the transient dead states cannot retain pipes or execute.
    const state = stat.slice(stat.lastIndexOf(')') + 2, stat.lastIndexOf(')') + 3);
    return ['Z', 'X', 'x'].includes(state);
  } catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return true;
    throw error;
  }
}
async function process_ids(path: string): Promise<OwnedProcesses> {
  const deadline = performance.now() + 3000;
  while (performance.now() < deadline) {
    try {
      return JSON.parse(readFileSync(path, 'utf8')) as OwnedProcesses;
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
    }
    await pause(10);
  }
  throw new Error('Scripted child did not report its owned process IDs');
}
async function group_stopped(owned: OwnedProcesses): Promise<void> {
  const deadline = performance.now() + 2000;
  let parent_stopped = stopped(owned.parent);
  let descendant_stopped = stopped(owned.descendant);
  while ((!parent_stopped || !descendant_stopped) && performance.now() < deadline) {
    await pause(10);
    if (!parent_stopped) parent_stopped = stopped(owned.parent);
    if (!descendant_stopped) descendant_stopped = stopped(owned.descendant);
  }
  // Keep the terminal sample instead of reopening /proc during its removal.
  expect(parent_stopped).toBe(true);
  expect(descendant_stopped).toBe(true);
}

// Distinct risk: a child can hold pipes through a grandchild after its own exit.
// Reuse Node itself, without a listener, provider CLI, database, or another test host.
test.skipIf(process.platform !== 'linux')(
  'commands join cancellation and shutdown, and close inherited pipes after normal parent exit',
  async () => {
    const root = mkdtempSync(join(tmpdir(), 'panels-backend-commands-'));
    const script = join(root, 'scripted-command.cjs');
    writeFileSync(
      script,
      [
        "const {spawn} = require('node:child_process');",
        "const {writeFileSync, renameSync} = require('node:fs');",
        "const descendant = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio: ['ignore', 'inherit', 'inherit']});",
        "descendant.once('spawn', () => {",
        "  writeFileSync(process.argv[2] + '.tmp', JSON.stringify({parent: process.pid, descendant: descendant.pid}));",
        "  renameSync(process.argv[2] + '.tmp', process.argv[2]);",
        "  if (process.argv[3] === 'exit') process.exit(0);",
        '  setInterval(() => {}, 1000);',
        '});',
      ].join('\n'),
    );
    const owned_groups: OwnedProcesses[] = [];
    const owners: BackendCommands[] = [];
    try {
      for (const mode of ['abort', 'shutdown', 'exit'] as const) {
        const commands = new BackendCommands();
        owners.push(commands);
        const controller = new AbortController();
        const marker = join(root, `${mode}.json`);
        // Observe rejection immediately: request abort and shutdown intentionally reject.
        const operation = commands
          .run([process.execPath, script, marker, mode], {
            timeout_seconds: 10,
            signal: controller.signal,
          })
          .then(
            (value) => ({ value }),
            (error: unknown) => ({ error }),
          );
        const owned = await process_ids(marker);
        owned_groups.push(owned);
        if (mode === 'abort') {
          const reason = new Error('Scripted request abort');
          controller.abort(reason);
          expect(await within(operation, 2000)).toEqual({ error: reason });
        } else if (mode === 'shutdown') {
          await within(commands.shutdown(), 2000);
          const settled = await operation;
          expect('error' in settled && settled.error).toBeInstanceOf(Error);
          if ('error' in settled)
            expect((settled.error as Error).message).toBe('Backend commands stopped at shutdown');
        } else {
          // The ten-second command timer must not be needed to release the held pipes.
          const settled = await within(operation, 2000);
          expect('value' in settled && settled.value.exit_code).toBe(0);
        }
        // run/shutdown settled only after the owned parent exited.
        expect(stopped(owned.parent)).toBe(true);
        await group_stopped(owned);
      }
    } finally {
      // Kill only the exact groups this test started, even if an assertion failed.
      for (const owned of owned_groups) {
        if (stopped(owned.parent) && stopped(owned.descendant)) continue;
        try {
          process.kill(-owned.parent, 'SIGKILL');
        } catch (error) {
          if (!(error instanceof Error && 'code' in error && error.code === 'ESRCH')) throw error;
        }
      }
      await within(Promise.allSettled(owners.map((owner) => owner.shutdown())), 3000);
      for (const owned of owned_groups) await group_stopped(owned);
      rmSync(root, { recursive: true, force: true });
    }
  },
  15_000,
);
