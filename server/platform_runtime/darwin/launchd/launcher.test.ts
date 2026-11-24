import { expect, test, vi } from 'vitest';
import { mkdtemp, writeFile, chmod, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { Socket } from 'node:net';
import { usable_worker_launcher, start_worker_job } from './launcher.ts';
import { WORKER_LAUNCHER_MODE, WORKER_LAUNCHER_FALLBACK_NOTICE } from './configuration.ts';
import { job_plist, await_worker_admission } from './transport.ts';

test('only an executable with Worker support selects the app, without invoking an old launcher', async () => {
  const root = await mkdtemp(join(tmpdir(), 'worker-launcher-'));
  const app = join(root, 'Panels');
  try {
    expect(await usable_worker_launcher(app)).toBeNull();
    await writeFile(app, '#!/bin/sh\nexit 99\n', { mode: 0o755 });
    expect(await usable_worker_launcher(app)).toBeNull();
    await writeFile(app, '--panels-worker-exec-v1\0');
    expect(await usable_worker_launcher(app)).toBeNull();
    await writeFile(app, `${WORKER_LAUNCHER_MODE}\0`);
    expect(await usable_worker_launcher(app)).toBe(app);
    await chmod(app, 0o600);
    expect(await usable_worker_launcher(app)).toBeNull();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('app plist retains release arguments, environment and launchd controls', async () => {
  const command = [
    '/release/node/bin/node',
    '/release/server/entry.ts',
    'record',
    'sockets',
    'token',
    '/provider',
    'a b',
    '<&',
  ];
  const attempt = vi.fn(async (argv: readonly string[]) =>
    job_plist(
      'com.panels.worker.test',
      argv,
      { NODE_OPTIONS: '--trace-warnings', NODE_PATH: '/custom path' },
      '/working',
      { lower_priority: true },
    ),
  );
  const notice = vi.fn();
  const plist = await start_worker_job({
    app: '/Applications/Panels.app/Contents/MacOS/Panels',
    command,
    attempt,
    remove_failed_attempt: vi.fn(),
    notice,
  });
  expect(attempt).toHaveBeenCalledExactlyOnceWith([
    '/Applications/Panels.app/Contents/MacOS/Panels',
    WORKER_LAUNCHER_MODE,
    ...command,
  ]);
  expect(plist).toContain('<string>a b</string><string>&lt;&amp;</string>');
  expect(plist).toContain('<key>NODE_OPTIONS</key><string>--trace-warnings</string>');
  expect(plist).toContain('<key>NODE_PATH</key><string>/custom path</string>');
  expect(plist).toContain('<key>WorkingDirectory</key><string>/working</string>');
  expect(plist).toContain('<key>Nice</key><integer>10</integer>');
  expect(plist).toContain('<key>AbandonProcessGroup</key><false/>');
  expect(notice).not.toHaveBeenCalled();
});

test('unusable app is removed before one bare Node attempt and one warning', async () => {
  const sequence: string[] = [];
  const command = ['/release/node', '/entry'];
  const attempt = vi.fn(async (argv: readonly string[]) => {
    sequence.push(argv[0]!);
    if (argv[0] === '/app') throw new Error('bootstrap or transport failure');
    return 'ready before admission';
  });
  const notice = vi.fn();
  expect(
    await start_worker_job({
      app: '/app',
      command,
      attempt,
      remove_failed_attempt: async () => {
        sequence.push('proven absent');
      },
      notice,
    }),
  ).toBe('ready before admission');
  expect(sequence).toEqual(['/app', 'proven absent', '/release/node']);
  expect(notice).toHaveBeenCalledExactlyOnceWith(WORKER_LAUNCHER_FALLBACK_NOTICE);
});

test('unproven removal or revoked authorization cannot trigger a second attempt', async () => {
  const attempt = vi.fn().mockRejectedValue(new Error('app failed'));
  const notice = vi.fn();
  await expect(
    start_worker_job({
      app: '/app',
      command: ['/node'],
      attempt,
      remove_failed_attempt: async () => {
        throw new Error('removal or authorization unproven');
      },
      notice,
    }),
  ).rejects.toThrow('unproven');
  expect(attempt).toHaveBeenCalledTimes(1);
  expect(notice).not.toHaveBeenCalled();
});

test('missing app warns once and a warning failure cannot prevent bare Node startup', async () => {
  const attempt = vi.fn().mockResolvedValue('ready');
  const notice = vi.fn(() => {
    throw new Error('logger failed');
  });
  expect(
    await start_worker_job({
      app: null,
      command: ['/node'],
      attempt,
      remove_failed_attempt: vi.fn(),
      notice,
    }),
  ).toBe('ready');
  expect(attempt).toHaveBeenCalledExactlyOnceWith(['/node']);
  expect(notice).toHaveBeenCalledTimes(1);
});

test('entry waits for the complete admission and rejects a disconnected or invalid attempt', async () => {
  const socket = new Socket();
  let admitted = false;
  const ready = await_worker_admission(socket).then(() => {
    admitted = true;
  });
  socket.emit('data', Buffer.from('sta'));
  await Promise.resolve();
  expect(admitted).toBe(false);
  socket.emit('data', Buffer.from('rt\n'));
  await ready;
  expect(admitted).toBe(true);
  socket.destroy();
  for (const event of ['end', 'close', 'error', 'data']) {
    const failed = new Socket();
    const promise = await_worker_admission(failed);
    const rejection = expect(promise).rejects.toThrow();
    failed.emit(event, event === 'data' ? Buffer.from('no\n') : new Error('disconnected'));
    await rejection;
    failed.destroy();
  }
});
