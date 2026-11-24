import { beforeEach, expect, test, vi } from 'vitest';
import type { WorkerProcess } from '../worker_supervision/contracts.ts';

const io = vi.hoisted(() => ({
  readFile: vi.fn(),
  readdir: vi.fn(),
  realpath: vi.fn(),
  stat: vi.fn(),
  run: vi.fn(),
  shutdown: vi.fn(),
}));
vi.mock('node:fs/promises', async (importOriginal) => ({
  ...(await importOriginal<typeof import('node:fs/promises')>()),
  readFile: io.readFile,
  readdir: io.readdir,
  realpath: io.realpath,
  stat: io.stat,
}));
vi.mock('./snapshot/commands.ts', () => ({
  BackendCommands: class {
    run = io.run;
    shutdown = io.shutdown;
  },
}));
import { inspect_legacy_agent_unit } from './legacy_agent_units.ts';

const unit = `panels-worker-${'a'.repeat(32)}.service`;
const path = `/sys/fs/cgroup/user.slice/panels-workers.slice/${unit}`;
const root: WorkerProcess = {
  pid: 20,
  birth: '120',
  parent: 1,
  group: 20,
  session: 20,
  zombie: false,
};
const child: WorkerProcess = { ...root, pid: 21, birth: '121', parent: 20 };
const stable = new Map([
  [20, root],
  [21, child],
]);

beforeEach(() => {
  vi.resetAllMocks();
  io.run.mockResolvedValue({
    exit_code: 0,
    standard_output: [
      'LoadState=loaded',
      'ActiveState=active',
      'Slice=panels-workers.slice',
      'KillMode=control-group',
      `ControlGroup=${path.slice('/sys/fs/cgroup'.length)}`,
    ].join('\n'),
  });
  io.shutdown.mockResolvedValue(undefined);
  io.realpath.mockImplementation(async (value: string) => value);
  io.stat.mockResolvedValue({ dev: 1n, ino: 2n });
  io.readdir.mockImplementation(async (directory: string) =>
    directory === path
      ? [
          {
            name: 'descendant',
            isDirectory: () => true,
            isSymbolicLink: () => false,
          },
        ]
      : [],
  );
  io.readFile.mockImplementation(async (name: string) => {
    if (name === `${path}/cgroup.procs`) return '20\n';
    if (name === `${path}/descendant/cgroup.procs`) return '21\n';
    const process = name === '/proc/20/stat' ? root : child;
    return `${process.pid} (agent) ${['S', process.parent, process.group, process.session, ...Array(15).fill('0'), process.birth].join(' ')}`;
  });
});

test('unit inventory includes descendant cgroups and uses only readonly service queries', async () => {
  expect(await inspect_legacy_agent_unit(unit, stable)).toEqual([root, child]);
  for (const [argv] of io.run.mock.calls)
    expect(argv.slice(0, 4)).toEqual(['systemctl', '--user', 'show', unit]);
  expect(io.shutdown).toHaveBeenCalledOnce();
});

test('unit inventory rejects replacement at the same cgroup path with unchanged properties', async () => {
  io.stat.mockResolvedValueOnce({ dev: 1n, ino: 2n }).mockResolvedValueOnce({ dev: 1n, ino: 3n });
  await expect(inspect_legacy_agent_unit(unit, stable)).rejects.toThrow('cgroup changed');
  expect(io.shutdown).toHaveBeenCalledOnce();
});

test('unit inventory refuses a foreign unit before any service query', async () => {
  await expect(inspect_legacy_agent_unit('other.service', stable)).rejects.toThrow(
    'Invalid historical Panels',
  );
  expect(io.run).not.toHaveBeenCalled();
});
