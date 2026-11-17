/** Real private files plus controlled service replies prove enrollment and rollback authority. */
import { mkdtemp, mkdir, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { write_credential } from '../../executor/credential_file.ts';
import type { ReleaseExecution } from '../release/contracts.ts';
import { run_release_command } from '../release/process.ts';
import type { LinuxExecutorJoinRequest } from './contracts.ts';
import { LINUX_EXECUTOR_SERVICE_NAME } from './configuration.ts';
import { install } from './install.ts';
import { install_linux_executor } from './join.ts';

vi.mock('./install.ts', () => ({ install: vi.fn() }));
vi.mock('../release/process.ts', async (original) => ({
  ...(await original<typeof import('../release/process.ts')>()),
  run_release_command: vi.fn(),
}));
const roots: string[] = [];
afterEach(async () => {
  vi.resetAllMocks();
  for (const root of roots.splice(0)) await rm(root, { recursive: true, force: true });
});
async function fixture() {
  const home = await mkdtemp(join(tmpdir(), 'linux-join-'));
  roots.push(home);
  const request: LinuxExecutorJoinRequest = {
    archive: join(home, 'archive'),
    archive_sha256: 'b'.repeat(64),
    archive_size: 123,
    revision: 'a'.repeat(40),
    machine_id: 'ad1f66de-bcaa-45b7-918a-8d0e7a018ae1',
    credential: 'x'.repeat(43),
    home_url: 'https://home.example',
    root: join(home, 'executor'),
    home,
    node_executable: process.execPath,
    isolated_providers: true,
  };
  const controller = new AbortController(),
    children = new ChildProcesses();
  const execution: ReleaseExecution = {
    child_processes: children,
    signal: controller.signal,
    deadline: performance.now() / 1000 + 60,
  };
  const unit = join(home, '.config/systemd/user', LINUX_EXECUTOR_SERVICE_NAME);
  const state = { loaded: false, interrupt_on_enable: false, fail_stop: false, health_calls: 0 };
  vi.mocked(install).mockImplementation(async (configuration, input) => {
    expect(input.credential_file.startsWith(request.root + '/')).toBe(false);
    expect((await stat(input.credential_file)).mode & 0o777).toBe(0o600);
    const grant = JSON.parse(await readFile(input.credential_file, 'utf8'));
    expect(grant.credential).toBe(request.credential);
    await mkdir(configuration.installation, { recursive: true });
    await mkdir(configuration.data);
    await write_credential(join(configuration.data, 'machine-credential.json'), grant.credential);
    await mkdir(input.unit_directory);
    const staged = join(input.unit_directory, LINUX_EXECUTOR_SERVICE_NAME);
    await writeFile(staged, '[Service]\n[Install]\n');
    return staged;
  });
  vi.mocked(run_release_command).mockImplementation(async (argv, options, scope) => {
    if (argv[0] !== 'systemctl') {
      state.health_calls++;
      expect(options.environment?.PLAN_DB_PATH).toBeUndefined();
      expect(options.environment?.PLAN_EXECUTOR_ROOT).toBe(join(request.root, 'data'));
      return {
        code: 0,
        stderr: '',
        stdout: JSON.stringify({
          identity: { application_revision: request.revision, protocol_version: 26 },
          last_lease_failure: null,
          going_to_sleep: false,
          admission_stopped: false,
          refusal: null,
          lease_accepted_at_mac_ms: state.health_calls === 1 ? 0 : Date.now() + 1,
        }),
      };
    }
    if (argv.includes('--property=LoadState'))
      return { code: 0, stdout: state.loaded ? 'loaded' : 'not-found', stderr: '' };
    if (argv.includes('enable')) {
      state.loaded = true;
      if (state.interrupt_on_enable) controller.abort(new Error('join interrupted'));
    }
    if (argv.includes('stop')) {
      expect(scope.signal.aborted).toBe(false);
      expect(scope.deadline).toBe(execution.deadline);
      if (state.fail_stop) return { code: 1, stdout: '', stderr: 'stop refused' };
      state.loaded = false;
    }
    if (argv.includes('--property=ActiveState'))
      return { code: 0, stdout: state.loaded ? 'active' : 'inactive', stderr: '' };
    return { code: 0, stdout: '', stderr: '' };
  });
  return { request, execution, controller, children, unit, state };
}
test('namespace preflight preserves foreign state and successful join waits for a fresh accepted lease', async () => {
  const value = await fixture();
  try {
    value.state.loaded = true;
    await expect(install_linux_executor(value.request, value.execution)).rejects.toThrow(
      'service already exists',
    );
    await expect(stat(value.request.root)).rejects.toMatchObject({ code: 'ENOENT' });
    value.state.loaded = false;
    await mkdir(join(value.request.home, '.config/systemd/user'), { recursive: true });
    await writeFile(value.unit, 'foreign unit');
    await expect(install_linux_executor(value.request, value.execution)).rejects.toThrow(
      'service already exists',
    );
    expect(await readFile(value.unit, 'utf8')).toBe('foreign unit');
    await rm(value.unit);
    await install_linux_executor(value.request, value.execution, {
      PLAN_DB_PATH: '/foreign/db',
      HOME: value.request.home,
    });
    expect(value.state.loaded).toBe(true);
    expect(value.state.health_calls).toBe(2);
    const text = await readFile(value.unit, 'utf8');
    expect(text).not.toContain(value.request.credential);
    for (const name of ['codex', 'claude']) {
      const path = join(value.request.root, 'providers', name);
      expect((await stat(path)).mode & 0o777).toBe(0o700);
      expect(text).toContain(path);
    }
    expect(
      await readFile(join(value.request.root, 'data/machine-credential.json'), 'utf8'),
    ).toContain(value.request.credential);
    const count = vi.mocked(run_release_command).mock.calls.length;
    await expect(install_linux_executor(value.request, value.execution)).rejects.toThrow(
      'root already exists',
    );
    expect(vi.mocked(run_release_command).mock.calls.length).toBe(count);
  } finally {
    value.children.seal();
    await value.children.settled();
  }
});
test('an interrupted join undoes its service with the original deadline and retains state when stop is uncertain', async () => {
  for (const fail_stop of [false, true]) {
    const value = await fixture();
    value.state.interrupt_on_enable = true;
    value.state.fail_stop = fail_stop;
    try {
      await expect(install_linux_executor(value.request, value.execution)).rejects.toThrow(
        fail_stop ? 'Retained installation' : 'join interrupted',
      );
      if (fail_stop) {
        expect(value.state.loaded).toBe(true);
        expect(await readFile(value.unit, 'utf8')).toContain('[Service]');
        expect(
          await readFile(join(value.request.root, 'data/machine-credential.json'), 'utf8'),
        ).toContain(value.request.credential);
      } else {
        expect(value.state.loaded).toBe(false);
        await expect(stat(value.unit)).rejects.toMatchObject({ code: 'ENOENT' });
        await expect(stat(value.request.root)).rejects.toMatchObject({ code: 'ENOENT' });
      }
    } finally {
      value.children.seal();
      await value.children.settled();
    }
  }
});
