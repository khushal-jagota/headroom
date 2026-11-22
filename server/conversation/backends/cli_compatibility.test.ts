/** The version probe owns and reaps exactly the executable it starts. */
import { EventEmitter } from 'node:events';
import { mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import { afterEach, expect, it, vi } from 'vitest';
import { spawn } from 'node:child_process';
import {
  cli_version_recorder,
  confirm_reported_cli_version,
  ensure_cli_compatible,
} from './cli_compatibility.ts';
import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';

vi.mock('node:child_process', () => ({ spawn: vi.fn(), spawnSync: vi.fn() }));
const directories: string[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.useRealTimers();
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true });
});
// Each test keys the module verdict cache by its own executable file.
function executable(): string {
  const directory = mkdtempSync(join(tmpdir(), 'cli-compatibility-'));
  directories.push(directory);
  const path = join(directory, 'claude');
  writeFileSync(path, 'synthetic');
  return path;
}

function child() {
  const process = Object.assign(new EventEmitter(), {
    pid: 424242,
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
  await vi.waitFor(() => expect(spawn).toHaveBeenCalledOnce());
  started.stdout.end('2.1.259 (Claude Code)\n');
  await Promise.resolve();
  expect(settled).toBe(false);
  started.exitCode = 0;
  started.emit('close', 0);
  await check;
  expect(spawn).toHaveBeenCalledWith('/synthetic/selected-cli', ['--version'], {
    env: { ...process.env, SELECTED_HOME: '/synthetic/home' },
    cwd: '/synthetic/workspace',
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: process.platform !== 'win32',
  });
  expect(started.kill).not.toHaveBeenCalled();
});

it('kills only its owned timed-out probe and joins its close before rejection', async () => {
  vi.useFakeTimers();
  const children = new OneShotChildren();
  const controller = new AbortController();
  const started = child();
  const signal_group = vi.spyOn(process, 'kill').mockReturnValue(true);
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
    'Cannot check the Claude version at /synthetic/blocked-cli. Update Claude to at least 2.1.259. Panels requires the prompt ids that each result names.',
  );
  await vi.waitFor(() => expect(spawn).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(10_000);
  expect(signal_group).toHaveBeenCalledExactlyOnceWith(-started.pid, 'SIGKILL');
  started.signalCode = 'SIGKILL';
  started.emit('exit', null, 'SIGKILL');
  controller.abort();
  expect(signal_group).toHaveBeenCalledTimes(1);
  await Promise.resolve();
  expect(settled).toBe(false);
  started.emit('close', null);
  await result;
  children.seal();
  await children.settled();
});

it('answers from the probe-kept verdict without a spawn until the file changes', async () => {
  const path = executable();
  (await cli_version_recorder('claude', path))('2.1.259 (Claude Code)\n', 0);
  await ensure_cli_compatible('claude', path);
  expect(spawn).not.toHaveBeenCalled();
  // An update replaces the file, so its identity no longer matches the kept verdict.
  utimesSync(path, new Date(0), new Date(0));
  const started = child();
  vi.mocked(spawn).mockReturnValue(started as never);
  const check = ensure_cli_compatible('claude', path);
  await vi.waitFor(() => expect(spawn).toHaveBeenCalledOnce());
  started.stdout.end('2.1.100 (Claude Code)\n');
  started.exitCode = 0;
  started.emit('close', 0);
  const too_old = `Claude 2.1.100 at ${path} is too old. Update Claude to at least 2.1.259. Panels requires the prompt ids that each result names.`;
  await expect(check).rejects.toThrow(too_old);
  await expect(ensure_cli_compatible('claude', path)).rejects.toThrow(too_old);
  expect(spawn).toHaveBeenCalledOnce();
});

it('lets a too-old handshake version replace a kept compatible verdict', async () => {
  const path = executable();
  (await cli_version_recorder('claude', path))('2.1.259 (Claude Code)\n', 0);
  await confirm_reported_cli_version('claude', path, undefined);
  await confirm_reported_cli_version('claude', path, '2.1.259');
  const too_old = `Claude 2.1.100 at ${path} is too old. Update Claude to at least 2.1.259. Panels requires the prompt ids that each result names.`;
  await expect(confirm_reported_cli_version('claude', path, '2.1.100')).rejects.toThrow(too_old);
  await expect(ensure_cli_compatible('claude', path)).rejects.toThrow(too_old);
  expect(spawn).not.toHaveBeenCalled();
});
