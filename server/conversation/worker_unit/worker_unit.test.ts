/** Manager control is mocked before import; subprocess APIs fail closed in this file. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';

const fake = vi.hoisted(() => ({
  control: vi.fn<(...args: string[]) => Promise<readonly [number, string]>>(),
  home: '',
  mounts: '1 0 0:1 / / rw - ext4 /dev/synthetic rw\n',
  spawn: vi.fn(() => {
    throw new Error('Containment tests prohibit all subprocess creation');
  }),
}));
vi.mock('./control.ts', () => ({ control: fake.control }));
vi.mock('node:child_process', () => ({
  spawn: fake.spawn,
  exec: fake.spawn,
  execFile: fake.spawn,
  execSync: fake.spawn,
  execFileSync: fake.spawn,
  spawnSync: fake.spawn,
  fork: fake.spawn,
}));
vi.mock('node:os', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:os')>()),
  homedir: () => fake.home,
}));
vi.mock('node:fs', async (importOriginal) => {
  const original = await importOriginal<typeof import('node:fs')>();
  return {
    ...original,
    readFileSync: (path: Parameters<typeof original.readFileSync>[0], ...options: unknown[]) => {
      if (path === '/proc/self/mountinfo') return fake.mounts;
      return Reflect.apply(original.readFileSync, original, [path, ...options]);
    },
  };
});
import { WorkerUnit, WorkerUnitError } from './index.ts';
import { with_tool_shell_kill_priority, TOOL_SHELL_STARTUP } from '../tool_shell_kill_priority.ts';

beforeEach(() => {
  fake.home = mkdtempSync(`${tmpdir()}/worker-unit-`);
  fake.mounts = '1 0 0:1 / / rw - ext4 /dev/synthetic rw\n';
  fake.control.mockReset();
  fake.spawn.mockClear();
});
afterEach(() => {
  expect(fake.spawn).not.toHaveBeenCalled();
  rmSync(fake.home, { recursive: true, force: true });
  vi.unstubAllEnvs();
});

const linux = describe.skipIf(process.platform !== 'linux');
linux('Worker containment policy, with no service-manager connection', () => {
  it('does not take inherited Worker identity as role identity', async () => {
    vi.stubEnv('PLAN_TICKET_ID', 'inherited-worker');
    expect(await WorkerUnit.create({}, fake.home)).toBeNull();
    expect(fake.control).not.toHaveBeenCalled();
  });

  it('refuses a missing lower-priority slice and never allocates uncontained state', async () => {
    fake.control.mockResolvedValue([0, 'LoadState=not-found\nCPUWeight=0']);
    await expect(
      WorkerUnit.create({ PLAN_TICKET_ID: 'synthetic-worker' }, fake.home),
    ).rejects.toThrow(
      'Worker slice panels-workers.slice is unavailable or lacks its lower CPU weight. ' +
        'Install the Worker slice before starting Workers.',
    );
    expect(fake.control.mock.calls).toEqual([
      ['show', 'panels-workers.slice', '--property=LoadState', '--property=CPUWeight'],
    ]);
  });

  it('requires the parent priority and disk storage before file creation', async () => {
    fake.control
      .mockResolvedValueOnce([0, 'LoadState=loaded\nCPUWeight=10'])
      .mockResolvedValueOnce([0, 'LoadState=loaded\nCPUWeight=100']);
    await expect(
      WorkerUnit.create({ PLAN_TICKET_ID: 'synthetic-worker' }, fake.home),
    ).rejects.toThrow('Install panels.slice with its lower CPU weight before Workers.');
    fake.control.mockResolvedValue([0, 'LoadState=loaded\nCPUWeight=10']);
    fake.mounts = '1 0 0:1 / / rw - tmpfs tmpfs rw\n';
    await expect(
      WorkerUnit.create({ PLAN_TICKET_ID: 'synthetic-worker' }, fake.home),
    ).rejects.toThrow(`Worker temporary storage must use disk: ${fake.home}`);
  });

  it('keeps private state and exact cleanup handles until stop succeeds', async () => {
    fake.control.mockResolvedValue([0, 'LoadState=loaded\nCPUWeight=10']);
    const unit = await WorkerUnit.create(
      { PLAN_TICKET_ID: 'synthetic-worker', VALUE: '$`\n' },
      fake.home,
    );
    expect(unit).not.toBeNull();
    if (unit === null) throw new Error('Expected owned synthetic unit');
    expect(unit.name).toMatch(/^panels-worker-[0-9a-f]{32}\.service$/);
    expect(statSync(unit.directory).mode & 0o777).toBe(0o700);
    expect(statSync(`${unit.directory}/t`).mode & 0o777).toBe(0o700);
    expect(statSync(`${unit.directory}/environment`).mode & 0o777).toBe(0o600);
    expect(unit.environment.TMPDIR).toBe(`${unit.directory}/t`);
    expect(unit.environment.TMP).toBe(unit.environment.TMPDIR);
    expect(unit.environment.TEMP).toBe(unit.environment.TMPDIR);
    expect(readFileSync(`${unit.directory}/environment`, 'utf8')).toContain('VALUE="\\$\\`\n"\n');
    fake.control.mockReset();
    fake.control
      .mockResolvedValueOnce([0, 'LoadState=loaded'])
      .mockResolvedValueOnce([1, 'synthetic refusal']);
    await expect(unit.stop()).rejects.toThrow(
      `Cannot stop Worker unit ${unit.name}: synthetic refusal`,
    );
    expect(existsSync(unit.directory)).toBe(true);
    fake.control.mockResolvedValue([0, 'LoadState=loaded']);
    await unit.stop();
    expect(existsSync(unit.directory)).toBe(false);
    expect(fake.control.mock.calls).toEqual([
      ['show', unit.name, '--property=LoadState'],
      ['stop', unit.name],
      ['show', unit.name, '--property=LoadState'],
      ['stop', unit.name],
      ['reset-failed', unit.name],
    ]);
    fake.control.mockClear();
    await unit.stop();
    expect(fake.control).not.toHaveBeenCalled();
  });

  it('retains private files when ownership cannot be inspected', async () => {
    const unit = new WorkerUnit({
      name: 'synthetic-owned.service',
      directory: `${fake.home}/owned`,
      environment: {},
      working_directory: fake.home,
      slice_name: 'synthetic.slice',
    });
    writeFileSync(unit.directory, 'owned marker');
    fake.control.mockResolvedValue([1, 'synthetic manager refusal']);
    await expect(unit.stop()).rejects.toBeInstanceOf(WorkerUnitError);
    expect(existsSync(unit.directory)).toBe(true);
    expect(fake.control.mock.calls).toEqual([
      ['show', 'synthetic-owned.service', '--property=LoadState'],
    ]);
  });

  it('selects the owned Bash startup file without modifying unrelated environment', () => {
    const environment = { ORIGINAL: 'preserved' };
    expect(with_tool_shell_kill_priority(environment)).toBe(environment);
    expect(environment).toEqual({ ORIGINAL: 'preserved', BASH_ENV: TOOL_SHELL_STARTUP });
    expect(readFileSync(TOOL_SHELL_STARTUP, 'utf8')).toContain('/proc/self/oom_score_adj');
  });
});
