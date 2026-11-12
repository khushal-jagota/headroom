/** The version probe owns and reaps exactly the executable it starts. */
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, expect, it, vi } from 'vitest';
import { spawn } from 'node:child_process';
import { ensure_cli_compatible } from './cli_compatibility.ts';
import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';

vi.mock('node:child_process', () => ({ spawn: vi.fn() }));
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.useRealTimers();
});

function child() {
  const process = Object.assign(new EventEmitter(), {
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    exitCode: null as number | null,
    signalCode: null as string | null,
    kill: vi.fn(() => true),
  });
  return process;
}

it('checks the selected executable with merged overrides and waits for close', async () => {
  const started = child();
  vi.mocked(spawn).mockReturnValue(started as never);
  const check = ensure_cli_compatible('claude', '/synthetic/selected-cli', {
    environment: { SELECTED_HOME: '/synthetic/home' },
    working_directory: '/synthetic/workspace',
  });
  let settled = false;
  void check.then(() => {
    settled = true;
  });
  started.stdout.end('2.1.219 (Claude Code)\n');
  await Promise.resolve();
  expect(settled).toBe(false);
  started.exitCode = 0;
  started.emit('close', 0);
  await check;
  expect(spawn).toHaveBeenCalledWith('/synthetic/selected-cli', ['--version'], {
    env: { ...process.env, SELECTED_HOME: '/synthetic/home' },
    cwd: '/synthetic/workspace',
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  expect(started.kill).not.toHaveBeenCalled();
});

it('kills only its owned timed-out probe and joins its close before rejection', async () => {
  vi.useFakeTimers();
  const children = new OneShotChildren();
  const controller = new AbortController();
  const started = child();
  vi.mocked(spawn).mockReturnValue(started as never);
  const check = ensure_cli_compatible('claude', '/synthetic/blocked-cli', {
    children,
    signal: controller.signal,
  });
  let settled = false;
  void check
    .finally(() => {
      settled = true;
    })
    .catch(() => {});
  const result = expect(check).rejects.toThrow(
    'Cannot check the Claude version at /synthetic/blocked-cli. Update Claude to at least 2.1.219. Panels requires queued message cancellation on Stop.',
  );
  await vi.advanceTimersByTimeAsync(10_000);
  expect(started.kill).toHaveBeenCalledExactlyOnceWith('SIGKILL');
  started.signalCode = 'SIGKILL';
  started.emit('exit', null, 'SIGKILL');
  controller.abort();
  await Promise.resolve();
  expect(settled).toBe(false);
  started.emit('close', null);
  await result;
  children.seal();
  await children.settled();
});
