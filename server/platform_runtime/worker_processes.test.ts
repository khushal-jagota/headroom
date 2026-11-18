/** Synthetic kernel evidence proves identity admission without host signals or live cgroups. */
import { afterEach, expect, test, vi } from 'vitest';
import { mkdtempSync, mkdirSync, openSync, fstatSync, writeFileSync, rmSync } from 'node:fs';
import { mkdir, readFile, stat, symlink, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { getPriority, tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { ChildProcesses, child_lifetime } from '../lifecycle/owned_processes.ts';
import { NICE } from '../worker_supervision/config.ts';
import { LinuxWorkerProcesses } from './linux/worker_processes.ts';
import { DarwinWorkerProcesses } from './darwin/worker_processes.ts';
import type { DarwinWorkerKernel } from './darwin/worker_processes.ts';
import { WorkerCgroups } from './linux/worker_cgroups.ts';
import { monotonic_seconds } from '../lifecycle/configuration.ts';
import { linux_worker_process_support } from '../lifecycle/linux_process.ts';
import {
  darwin_worker_process_info,
  darwin_worker_process_snapshot,
} from '../executor/mac/platform.ts';
import type { WorkerProcess, CgroupIdentity } from '../worker_supervision/contracts.ts';

const fake_binding = vi.hoisted(() => {
  const library = {
    func: vi.fn(
      () =>
        (..._args: unknown[]) =>
          0,
    ),
  };
  return { load: vi.fn((_path: string) => library), errno: vi.fn((_value?: number) => 0) };
});
vi.mock('koffi', () => ({ default: fake_binding }));

const roots: string[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function root(): string {
  const value = mkdtempSync(join(tmpdir(), 'worker-kernel-'));
  roots.push(value);
  return value;
}
function proc_stat(birth = '9007199254740993', status = 'S'): string {
  const fields = Array.from({ length: 20 }, () => '0');
  [fields[0], fields[1], fields[2], fields[3], fields[19]] = [status, '1', '42', '42', birth];
  return '42 (name has ) spaces) ' + fields.join(' ');
}
const process_info: WorkerProcess = {
  pid: 42,
  birth: '9007199254740993',
  parent: 1,
  group: 42,
  session: 42,
  zombie: false,
};

test('Linux proc identities retain exact birth ticks and unreadable entries never prove absence', async () => {
  const path = root();
  mkdirSync(join(path, '42'));
  writeFileSync(join(path, '42/stat'), proc_stat());
  const native = new LinuxWorkerProcesses({ proc_root: path, clock: () => 0 });
  expect((await native.snapshot(1)).get(42)).toEqual(process_info);
  expect(await native.is_absent(process_info, 1)).toBe(false);
  writeFileSync(join(path, '42/stat'), proc_stat('9007199254740994'));
  expect(await native.is_absent(process_info, 1)).toBe(true);
  writeFileSync(join(path, '42/stat'), 'unreadable');
  await expect(native.is_absent(process_info, 1)).rejects.toThrow('unreadable');
  await expect(native.snapshot(0)).rejects.toThrow('deadline');
});

test.skipIf(process.platform !== 'linux')(
  'Linux launch preparation lowers only a Worker priority and always isolates the session',
  async () => {
    // The child inherits this process's real nice value, which only rises. The OOM score is
    // a synthetic file below zero, so it shows the clamp even where the host's score is zero.
    const script = `import { LinuxWorkerProcesses } from ${JSON.stringify(
      pathToFileURL(join(import.meta.dirname, 'linux/worker_processes.ts')).href,
    )};
import { getPriority } from 'node:os';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
const proc_root = mkdtempSync(join(process.argv[2], 'proc-'));
mkdirSync(join(proc_root, 'self'));
writeFileSync(join(proc_root, 'self/oom_score_adj'), '-500\\n');
let session = false;
new LinuxWorkerProcesses({ proc_root, native: () => ({ prepare_session: () => { session = true; } }) })
  .prepare_launch({ lower_priority: process.argv[1] === 'true' });
const oom = Number(readFileSync(join(proc_root, 'self/oom_score_adj'), 'utf8'));
process.stdout.write(JSON.stringify({ session, nice: getPriority(0), oom }));`;
    const directory = root();
    const children = new ChildProcesses();
    const prepared = async (lower_priority: boolean) => {
      const child = children.spawn(
        process.execPath,
        ['--input-type=module', '-e', script, String(lower_priority), directory],
        { stdio: ['ignore', 'pipe', 'inherit'] },
      );
      let output = '';
      child.stdout!.on('data', (chunk: Buffer) => (output += chunk.toString('utf8')));
      await child_lifetime(child).closed;
      return JSON.parse(output) as { session: boolean; nice: number; oom: number };
    };
    try {
      const nice = getPriority(0);
      expect(await prepared(false)).toEqual({ session: true, nice, oom: -500 });
      expect(await prepared(true)).toEqual({ session: true, nice: Math.max(NICE, nice), oom: 0 });
    } finally {
      children.seal();
      await children.settled();
    }
  },
);

test('Linux process check finishes its /proc reads before a busy main thread can expire the deadline', async () => {
  const path = root();
  mkdirSync(join(path, '42'));
  writeFileSync(join(path, '42/stat'), proc_stat());
  const native = new LinuxWorkerProcesses({ proc_root: path });
  const deadline = monotonic_seconds() + 2;
  const snapshot = native.snapshot(deadline);
  const is_absent = native.is_absent(process_info, deadline);
  const end = Date.now() + 2500; // the main thread is blocked past the 2-second deadline
  while (Date.now() < end);
  expect((await snapshot).get(42)).toEqual(process_info);
  expect(await is_absent).toBe(false);
});

test('Linux signals acquire the pidfd before the final birth check and never signal a replacement', () => {
  const path = join(root(), 'descriptor');
  writeFileSync(path, 'synthetic');
  const calls: string[] = [];
  let descriptor = -1;
  fake_binding.load.mockReturnValue({
    func: (signature: string) => {
      if (signature === 'long syscall(long number, ...)')
        return (number: number, ...args: unknown[]) => {
          if (number === 434) {
            expect(args).toEqual(['long', 42, 'unsigned long', 0]);
            calls.push('open');
            return (descriptor = openSync(path, 'r'));
          }
          expect(number).toBe(424);
          expect(args).toEqual([
            'long',
            descriptor,
            'long',
            15,
            'void *',
            null,
            'unsigned long',
            0,
          ]);
          calls.push('signal');
          return 0;
        };
      if (signature.includes('pidfd_')) throw new Error('glibc pidfd wrapper is absent');
      return () => 0;
    },
  } as unknown as ReturnType<typeof fake_binding.load>);
  const support = linux_worker_process_support();
  expect(
    support.signal_exact(42, process_info.birth, 15, () => {
      calls.push('birth');
      return process_info;
    }),
  ).toBe(true);
  expect(calls).toEqual(['open', 'birth', 'signal']);
  expect(() => fstatSync(descriptor)).toThrow();
  calls.length = 0;
  expect(
    support.signal_exact(42, process_info.birth, 9, () => {
      calls.push('birth');
      return { ...process_info, birth: 'replacement' };
    }),
  ).toBe(true);
  expect(calls).toEqual(['open', 'birth']);
});

test('Darwin libproc decoding retains microseconds, a shared clock, and refuses a truncated inventory', async () => {
  const original = process.platform;
  let truncated = true;
  Object.defineProperty(process, 'platform', { value: 'darwin' });
  try {
    fake_binding.load.mockReturnValue({
      func: (signature: string) => {
        if (signature.includes('proc_pidinfo'))
          return (_pid: number, _flavor: number, _arg: number, buffer: Buffer) => {
            buffer.writeUInt32LE(1, 16);
            buffer.writeUInt32LE(42, 100);
            buffer.writeBigUInt64LE(9007199254740993n, 120);
            buffer.writeBigUInt64LE(123456n, 128);
            return buffer.length;
          };
        if (signature.includes('proc_listpids'))
          return (_type: number, _uid: number, buffer: Buffer | null) => {
            if (buffer === null) return 4;
            if (truncated) return buffer.length;
            buffer.writeInt32LE(42);
            return 4;
          };
        return () => 0;
      },
    } as unknown as ReturnType<typeof fake_binding.load>);
    expect(darwin_worker_process_info(42)?.birth).toBe('9007199254740993:123456');
    expect(() => darwin_worker_process_snapshot(Infinity)).toThrow('incomplete');
    truncated = false;
    const clock = vi.fn(() => 0);
    expect(darwin_worker_process_snapshot(1, clock).get(42)?.birth).toBe('9007199254740993:123456');
    expect(clock).toHaveBeenCalled();
    const native = new DarwinWorkerProcesses({ commands: { run: vi.fn() }, clock });
    // Both the facade and its default bridge must use the same injected clock.
    await expect(native.snapshot(1)).resolves.toHaveProperty('size', 1);
    await expect(native.snapshot(0)).rejects.toThrow('deadline');
  } finally {
    Object.defineProperty(process, 'platform', { value: original });
  }
});

test('Darwin signals require an immediate exact birth and unreadable evidence remains uncertain', async () => {
  let current = { ...process_info, birth: '10:20' };
  const signals: number[] = [];
  const kernel: DarwinWorkerKernel = {
    info: () => current,
    birth: () => current.birth,
    exists: () => true,
    snapshot: () => new Map(),
    signal: (_pid, signum) => {
      signals.push(signum);
    },
    prepare_session() {},
  };
  const native = new DarwinWorkerProcesses({
    commands: { run: vi.fn() },
    native: kernel,
    clock: () => 0,
  });
  const recorded = { ...process_info, birth: '10:20' };
  expect(await native.signal(recorded, 15, 1)).toBe(true);
  current = { ...current, birth: '10:21' };
  expect(await native.signal(recorded, 9, 1)).toBe(false);
  expect(await native.is_absent(recorded, 1)).toBe(true);
  kernel.info = () => {
    throw Object.assign(new Error('unreadable'), { code: 'EPERM' });
  };
  expect(await native.signal(recorded, 9, 1)).toBe(false);
  expect(await native.is_absent(recorded, 1)).toBe(true);
  kernel.birth = () => {
    throw Object.assign(new Error('unreadable sysctl'), { code: 'EPERM' });
  };
  expect(await native.is_absent(recorded, 1)).toBe(false);
  kernel.exists = () => {
    throw Object.assign(new Error('foreign UID'), { code: 'EPERM' });
  };
  kernel.birth = () => current.birth;
  // A second denied libproc read must not hide a changed sysctl birth.
  expect(await native.is_absent(recorded, 1)).toBe(true);
  kernel.birth = () => recorded.birth;
  expect(await native.is_absent(recorded, 1)).toBe(false);
  kernel.birth = () => {
    throw Object.assign(new Error('unreadable sysctl'), { code: 'EPERM' });
  };
  expect(await native.is_absent(recorded, 1)).toBe(false);
  expect(signals).toEqual([15]);
});

test('Mac host identity gives a longer waiter its own probe after the short owner fails', async () => {
  const value = '11111111-1111-1111-1111-111111111111';
  let now = 0;
  const failed = Promise.withResolvers<{
    exit_code: number;
    standard_error: string;
    standard_output: string;
  }>();
  const short_run = vi.fn(() => failed.promise);
  const long_run = vi.fn(
    async (command: readonly string[], _options: { timeout_seconds: number }) => ({
      exit_code: 0,
      standard_error: '',
      standard_output: command[0].endsWith('sysctl') ? value : `"IOPlatformUUID" = "${value}"`,
    }),
  );
  const short = new DarwinWorkerProcesses({
    commands: { run: short_run },
    clock: () => now,
  }).host_boot(0.25);
  const refused = expect(short).rejects.toThrow('Mac host identity command failed');
  const long = new DarwinWorkerProcesses({
    commands: { run: long_run },
    clock: () => now,
  }).host_boot(2);
  now = 0.25;
  failed.reject(new Error('synthetic short owner timeout'));
  await refused;
  const identity = { host: `darwin:${value}`, boot: `darwin:${value}` };
  expect(await long).toEqual(identity);
  expect(short_run).toHaveBeenCalledTimes(1);
  expect(long_run).toHaveBeenCalledTimes(2);
  expect(long_run.mock.calls.map((call) => call[1].timeout_seconds)).toEqual([1.75, 1.75]);
  expect(
    await new DarwinWorkerProcesses({ commands: { run: long_run }, clock: () => now }).host_boot(2),
  ).toEqual(identity);
  expect(long_run).toHaveBeenCalledTimes(2);
});

async function cgroup_fixture() {
  const hierarchy = join(root(), 'hierarchy');
  const parent = join(hierarchy, 'service');
  await mkdir(parent, { recursive: true });
  await writeFile(join(hierarchy, 'cgroup.controllers'), 'cpu memory');
  const info = await stat(hierarchy, { bigint: true });
  const identity: CgroupIdentity = {
    path: join(parent, 'panels-worker-' + 'a'.repeat(32)),
    inode: 123n,
    root_device: info.dev,
    root_inode: info.ino,
  };
  return { hierarchy, parent, identity, native: new WorkerCgroups({ root: hierarchy }) };
}
test('missing cgroup proves emptiness only inside its original readable hierarchy', async () => {
  const { hierarchy, parent, identity, native } = await cgroup_fixture();
  expect(await native.empty(identity)).toBe(true);
  await expect(native.empty({ ...identity, root_inode: identity.root_inode + 1n })).rejects.toThrow(
    'hierarchy identity',
  );
  await mkdir(identity.path);
  const group = { ...identity, inode: (await stat(identity.path, { bigint: true })).ino };
  await expect(native.empty(group)).rejects.toMatchObject({ code: 'ENOENT' });
  await expect(native.kill(group)).rejects.toMatchObject({ code: 'ENOENT' });
  await writeFile(join(identity.path, 'cgroup.events'), 'populated 1\n');
  expect(await native.empty(group)).toBe(false);
  await writeFile(join(identity.path, 'cgroup.events'), 'populated 0\n');
  expect(await native.empty(group)).toBe(true);
  await writeFile(join(identity.path, 'cgroup.events'), 'populated unknown\n');
  await expect(native.empty(group)).rejects.toThrow('population evidence');
  await symlink(identity.path, join(parent, 'alias'));
  await expect(
    native.empty({ ...group, path: join(parent, 'alias', 'panels-worker-' + 'b'.repeat(32)) }),
  ).rejects.toThrow('identity');
  await unlink(join(hierarchy, 'cgroup.controllers'));
  await expect(native.empty(identity)).rejects.toMatchObject({ code: 'ENOENT' });
});

test('cgroup custody preserves distinct device and inode identities above two to the fifty-third', async () => {
  const { hierarchy, parent } = await cgroup_fixture();
  const membership = join(root(), 'membership');
  await writeFile(membership, '0::/service\n');
  await writeFile(join(parent, 'cgroup.subtree_control'), '');
  const high = 9007199254740993n;
  // The synthetic stat bridge supplies kernel identities. No live hierarchy is used.
  const native = new WorkerCgroups({
    root: hierarchy,
    self_cgroup: membership,
    stat_identity: async (path) => ({ dev: high + 1n, ino: path === hierarchy ? high : high + 2n }),
  });
  const identity = await native.create('c'.repeat(32));
  expect(identity).toMatchObject({ inode: high + 2n, root_device: high + 1n, root_inode: high });
  await writeFile(join(identity.path, 'cgroup.events'), 'populated 0\n');
  expect(await native.empty(identity)).toBe(true);
  await expect(native.empty({ ...identity, inode: identity.inode + 1n })).rejects.toThrow(
    'identity',
  );
  await expect(native.empty({ ...identity, root_inode: identity.root_inode + 1n })).rejects.toThrow(
    'hierarchy identity',
  );
});

test('creation refuses controllers and missing kernel kill support without leaking a synthetic directory', async () => {
  const { hierarchy, parent } = await cgroup_fixture();
  const membership = join(root(), 'membership');
  await writeFile(membership, '0::/service\n');
  const native = new WorkerCgroups({ root: hierarchy, self_cgroup: membership });
  const subtree = join(parent, 'cgroup.subtree_control');
  await writeFile(subtree, 'cpu');
  await expect(native.create('b'.repeat(32))).rejects.toThrow('requires a server leaf');
  await writeFile(subtree, '');
  await expect(native.create('b'.repeat(32))).rejects.toThrow('cgroup.kill is unavailable');
  await expect(
    readFile(join(parent, 'panels-worker-' + 'b'.repeat(32), 'cgroup.kill')),
  ).rejects.toMatchObject({ code: 'ENOENT' });
});
