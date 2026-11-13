/** No native subprocess API is imported through this mock. */
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CONTROL_TIMEOUT_SECONDS } from './configuration.ts';

const fake = vi.hoisted(() => ({
  spawn: vi.fn<(...args: unknown[]) => unknown>(() => {
    throw new Error('A test must supply its owned fake process before manager control');
  }),
}));
vi.mock('node:child_process', () => ({ spawn: fake.spawn, spawnSync: vi.fn() }));
import { control } from './control.ts';

function owned_child() {
  return Object.assign(new EventEmitter(), {
    stdout: new PassThrough(),
    stderr: new PassThrough(),
    kill: vi.fn(() => true),
  });
}

beforeEach(() => fake.spawn.mockReset());
afterEach(() => vi.useRealTimers());

it('reports missing systemctl with the Python native error text', async () => {
  const child = owned_child();
  fake.spawn.mockImplementation(() => child);
  const pending = control(
    new ChildProcesses(),
    'show',
    'synthetic-owned.service',
    '--property=LoadState',
  );
  const checked = expect(pending).rejects.toThrow(
    "Cannot contact the Worker service manager: [Errno 2] No such file or directory: 'systemctl'",
  );
  child.emit(
    'error',
    Object.assign(new Error('spawn systemctl ENOENT'), {
      errno: -2,
      code: 'ENOENT',
      syscall: 'spawn systemctl',
      path: 'systemctl',
    }),
  );
  await checked;
  child.emit('close', -2, null);
  expect(fake.spawn).toHaveBeenCalledExactlyOnceWith(
    'systemctl',
    ['--user', 'show', 'synthetic-owned.service', '--property=LoadState'],
    { stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32' },
  );
  expect(child.kill).not.toHaveBeenCalled();
});

it('kills only its timed-out child and waits for exact exit evidence', async () => {
  vi.useFakeTimers();
  const child = owned_child();
  fake.spawn.mockImplementation(() => child);
  const pending = control(
    new ChildProcesses(),
    'show',
    'synthetic-owned.service',
    '--property=LoadState',
  );
  let settled = false;
  void pending.then(
    () => {
      settled = true;
    },
    () => {
      settled = true;
    },
  );
  const checked = expect(pending).rejects.toMatchObject({ name: 'TimeoutError', message: '' });
  await vi.advanceTimersByTimeAsync(CONTROL_TIMEOUT_SECONDS * 1000);
  expect(child.kill).toHaveBeenCalledExactlyOnceWith('SIGKILL');
  expect(settled).toBe(false);
  child.emit('close', null, 'SIGKILL');
  await checked;
  expect(settled).toBe(true);
  expect(fake.spawn).toHaveBeenCalledTimes(1);
});
