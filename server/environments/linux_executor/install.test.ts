/** Admission and exclusive publication protect private state and foreign files. */
import { chmod, mkdtemp, mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import type { ReleaseExecution } from '../release/contracts.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { validate_app_manifest } from '../release/identity.ts';
import { find_release_executable, run_release_command } from '../release/process.ts';
import { linux_executor_path } from './logic/paths.ts';
import { extract_runtime } from './archive.ts';
import type { LinuxExecutorInstallation, LinuxExecutorInstallRequest } from './contracts.ts';
import { LINUX_EXECUTOR_SERVICE_NAME } from './configuration.ts';
import { install } from './install.ts';
import { render_service } from './units.ts';

vi.mock('./archive.ts', () => ({ extract_runtime: vi.fn() }));
vi.mock('../release/identity.ts', async (original) => ({
  ...(await original<typeof import('../release/identity.ts')>()),
  validate_app_manifest: vi.fn(),
}));
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
  const root = await mkdtemp(join(tmpdir(), 'linux-executor-install-'));
  roots.push(root);
  const configuration: LinuxExecutorInstallation = {
    installation: join(root, 'installation'),
    data: join(root, 'data'),
    executor_id: 'linux-test',
    server_url: 'https://home.example',
    revision: 'a'.repeat(40),
    home: root,
    node_executable: process.execPath,
  };
  const request: LinuxExecutorInstallRequest = {
    archive: join(root, 'archive.tar.gz'),
    archive_sha256: 'b'.repeat(64),
    archive_size: 123,
    credential_file: join(root, 'credential.json'),
    unit_directory: join(root, 'units'),
  };
  await writeFile(request.credential_file, JSON.stringify({ credential: 'a'.repeat(43) }));
  vi.mocked(validate_app_manifest).mockResolvedValue({
    runtime_contract: {
      version: 2,
      system: 'linux',
      node_major: 22,
      minimum_node_version: '22.18.0',
    },
  } as Awaited<ReturnType<typeof validate_app_manifest>>);
  vi.mocked(run_release_command).mockResolvedValue({ code: 0, stdout: 'v22.18.0\n', stderr: '' });
  const runtime = join(configuration.installation, 'releases/new-runtime');
  vi.mocked(extract_runtime).mockImplementation(async () => {
    await mkdir(runtime);
    await writeFile(join(runtime, 'payload'), 'release');
    return runtime;
  });
  const children = new ChildProcesses();
  const execution: ReleaseExecution = {
    child_processes: children,
    signal: new AbortController().signal,
    deadline: null,
  };
  return { root, configuration, request, runtime, execution, children };
}
test('archive or ExecStart Node refusal precedes credential and unit writes', async () => {
  const value = await fixture();
  try {
    vi.mocked(extract_runtime).mockRejectedValueOnce(new Error('wrong archive role'));
    await expect(install(value.configuration, value.request, value.execution)).rejects.toThrow(
      'wrong archive role',
    );
    await expect(stat(value.configuration.data)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect(stat(value.request.unit_directory)).rejects.toMatchObject({ code: 'ENOENT' });
    vi.mocked(run_release_command).mockResolvedValueOnce({
      code: 0,
      stdout: 'v20.0.0\n',
      stderr: '',
    });
    await expect(install(value.configuration, value.request, value.execution)).rejects.toThrow(
      'runtime requires Node 22',
    );
    expect(vi.mocked(run_release_command).mock.calls[0]?.[0]).toEqual([
      process.execPath,
      '--version',
    ]);
    await expect(stat(value.runtime)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect(stat(value.configuration.data)).rejects.toMatchObject({ code: 'ENOENT' });
    await expect(stat(value.request.unit_directory)).rejects.toMatchObject({ code: 'ENOENT' });
  } finally {
    value.children.seal();
    await value.children.settled();
  }
});
test('late unit collision preserves foreign files, restores empty data, and permits a clean retry', async () => {
  const value = await fixture(),
    config = value.configuration;
  try {
    await mkdir(config.installation);
    await writeFile(join(config.installation, 'keep'), 'foreign installation');
    await mkdir(config.data);
    await chmod(config.data, 0o755);
    await mkdir(value.request.unit_directory);
    const unit = join(value.request.unit_directory, LINUX_EXECUTOR_SERVICE_NAME);
    const extract = vi.mocked(extract_runtime).getMockImplementation()!;
    vi.mocked(extract_runtime).mockImplementationOnce(async (...args) => {
      const runtime = await extract(...args);
      await writeFile(unit, 'foreign unit');
      return runtime;
    });
    await expect(install(config, value.request, value.execution)).rejects.toMatchObject({
      code: 'EEXIST',
    });
    expect(await readFile(unit, 'utf8')).toBe('foreign unit');
    expect(await readFile(join(config.installation, 'keep'), 'utf8')).toBe('foreign installation');
    expect(await readdir(config.data)).toEqual([]);
    expect((await stat(config.data)).mode & 0o777).toBe(0o755);
    await rm(unit);
    expect(await install(config, value.request, value.execution)).toBe(unit);
    expect(await readFile(unit, 'utf8')).toBe(render_service(config, value.runtime));
    expect(await readFile(unit, 'utf8')).toContain(
      'UnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH\n',
    );
    const credential = join(config.data, 'machine-credential.json');
    expect((await stat(credential)).mode & 0o777).toBe(0o600);
    expect(JSON.parse(await readFile(credential, 'utf8')).credential).toBe('a'.repeat(43));
    const count = vi.mocked(extract_runtime).mock.calls.length;
    await expect(install(config, value.request, value.execution)).rejects.toThrow(
      'already installed',
    );
    expect(vi.mocked(extract_runtime).mock.calls.length).toBe(count);
    expect(await readFile(unit, 'utf8')).toBe(render_service(config, value.runtime));
  } finally {
    value.children.seal();
    await value.children.settled();
  }
});

// Risk: a provider beside Node or in the release shadows the account's logged-in command.
// Existing install tests check service writes, but do not resolve colliding executables.
test('machine provider lookup prefers the account over Node and packaged commands', async () => {
  const value = await fixture();
  const account = join(value.root, '.local/bin');
  const node = join(value.root, 'host-node/bin');
  const packaged = join(value.runtime, 'agent_backends/node_modules/.bin');
  for (const directory of [account, node, packaged]) {
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, 'codex'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  }
  const environment = { PATH: linux_executor_path(value.runtime, join(node, 'node'), value.root) };
  expect(await find_release_executable('codex', environment)).toBe(join(account, 'codex'));
});
