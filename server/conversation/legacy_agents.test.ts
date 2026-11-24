import BetterSqlite3 from 'better-sqlite3';
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { inspect_legacy_agent_unit } from './legacy_agent_units.ts';
vi.mock('./legacy_agent_units.ts', () => ({ inspect_legacy_agent_unit: vi.fn() }));
import { inspect_legacy_agents, plan_legacy_agents } from './legacy_agents.ts';
import { run } from '../cli/main.ts';
import { index_processes } from '../worker_supervision/observation.ts';
import { LinuxWorkerProcesses } from '../platform_runtime/linux/worker_processes.ts';
import type { LocalChildCustodyRow } from './local_child_custody_contracts.ts';
import type { LegacyAgentObservation } from './legacy_agent_contracts.ts';

const row: LocalChildCustodyRow = {
  conversation_id: 'old-panels',
  generation: 'old',
  state: 'started',
  pid: 42,
  birth: '123',
  boot: 'boot',
  host: 'linux:host',
  unit: null,
  runtime_epoch: null,
  occurrence_id: null,
  supervision_record: null,
};
const process = { pid: 42, birth: '123', parent: 1, group: 42, session: 42, zombie: false };
function observation(birth = '123'): LegacyAgentObservation {
  const stable = new Map([[42, { ...process, birth }]]);
  return {
    host: { host: 'linux:host', boot: 'boot' },
    processes: {
      stable,
      ...index_processes(stable),
      latest_sessions: index_processes(stable).sessions,
    },
    details: async (pid) => ({
      pid,
      birth,
      command: 'old agent',
      owner: 'uid:1000',
      age_seconds: 10,
    }),
    journal: async () => ({
      version: 1,
      ticket_id: 'ticket',
      conversation_id: row.conversation_id,
      host: row.host!,
      boot: row.boot!,
      root: process,
      processes: [process],
      state: 'running',
    }),
  };
}
test('dry-run only reports exact old custody incarnations and separates uncertain records', async () => {
  const proven = await plan_legacy_agents([row], observation());
  expect(proven.would_stop).toEqual([
    {
      pid: 42,
      birth: '123',
      command: 'old agent',
      owner: 'uid:1000',
      age_seconds: 10,
      record: 'conversation_child_custody:old-panels:old',
    },
  ]);
  for (const changed of [
    { ...row, host: 'foreign' },
    { ...row, boot: 'old' },
    { ...row, birth: 'reused' },
    { ...row, pid: 99 },
  ]) {
    const report = await plan_legacy_agents([changed], observation());
    expect(report.would_stop).toEqual([]);
    expect(report.unproven).toHaveLength(1);
  }
  const journal = await plan_legacy_agents(
    [{ ...row, supervision_record: 'journal' }],
    observation(),
  );
  expect(journal.would_stop[0].record).toContain(' -> journal');
  const mismatch = observation();
  mismatch.journal = async () => ({
    version: 1,
    ticket_id: 'ticket',
    host: 'foreign',
    boot: row.boot!,
    processes: [process],
    root: process,
  });
  expect(
    (await plan_legacy_agents([{ ...row, supervision_record: 'journal' }], mismatch)).would_stop,
  ).toEqual([]);
});

test.runIf(globalThis.process.platform === 'linux')(
  'CLI reads a private WAL snapshot and preserves source database, sidecars and live process',
  async () => {
    const root = await mkdtemp(join(tmpdir(), 'legacy-agent-cli-'));
    const path = join(root, 'database.sqlite');
    const db = new BetterSqlite3(path);
    try {
      db.pragma('journal_mode=WAL');
      db.exec(
        'CREATE TABLE conversation_child_custody (conversation_id TEXT, generation TEXT, state TEXT, pid INTEGER, birth TEXT, boot TEXT, host TEXT, unit TEXT, runtime_epoch TEXT, occurrence_id TEXT, supervision_record TEXT)',
      );
      const native = new LinuxWorkerProcesses();
      const host = await native.host_boot(Infinity);
      const current = (await native.snapshot(Infinity)).get(globalThis.process.pid)!;
      const live = { ...row, ...host, pid: current.pid, birth: current.birth };
      db.prepare(
        'INSERT INTO conversation_child_custody VALUES (@conversation_id,@generation,@state,@pid,@birth,@boot,@host,@unit,@runtime_epoch,@occurrence_id,@supervision_record)',
      ).run(live);
      const journal_path = join(root, 'a'.repeat(32) + '.json');
      await writeFile(
        journal_path,
        JSON.stringify({
          version: 1,
          ticket_id: 'old-ticket',
          conversation_id: 'supervised-old',
          host: host.host,
          boot: host.boot,
          root: current,
          processes: [current],
          state: 'running',
        }),
        { mode: 0o600 },
      );
      db.prepare(
        'INSERT INTO conversation_child_custody VALUES (@conversation_id,@generation,@state,@pid,@birth,@boot,@host,@unit,@runtime_epoch,@occurrence_id,@supervision_record)',
      ).run({ ...live, conversation_id: 'supervised-old', supervision_record: journal_path });
      const inventory = async () => {
        const files = (await readdir(root)).sort();
        // Bytes, size and mtime of the database, its -wal and its -shm.
        return Promise.all(
          files.map(async (file) => {
            const info = await stat(join(root, file));
            return [
              file,
              info.size,
              info.mtimeMs,
              (await readFile(join(root, file))).toString('base64'),
            ];
          }),
        );
      };
      const before = await inventory();
      expect(before.map(([file]) => file)).toEqual(
        expect.arrayContaining(['database.sqlite', 'database.sqlite-shm', 'database.sqlite-wal']),
      );
      const result = await run(['legacy-agents', 'dry-run', '--database-path', path], {});
      expect(result.exit_code).toBe(0);
      const report = JSON.parse(result.stdout);
      expect(report.would_stop).toHaveLength(2);
      expect(report.would_stop[0]).toMatchObject({
        pid: globalThis.process.pid,
        birth: current.birth,
      });
      expect(report.would_stop[0].command).toBeTruthy();
      expect(report.would_stop[0].owner).toMatch(/^uid:\d+$/);
      expect(report.would_stop[0].age_seconds).toBeGreaterThanOrEqual(0);
      // The database and its WAL are unchanged in bytes, size and mtime. The -shm keeps its
      // size. As any WAL reader, the read-only backup may set a read mark, which can also move
      // the -shm mtime: only the aReadMark slots of the checkpoint header (bytes 100 to 119)
      // may differ.
      const after = await inventory();
      expect(after.map(([file]) => file)).toEqual(before.map(([file]) => file));
      for (const [index, entry] of after.entries()) {
        const [file, size, mtime, bytes] = entry as [string, number, number, string];
        const [, size_before, mtime_before, bytes_before] = before[index] as [
          string,
          number,
          number,
          string,
        ];
        expect(size).toBe(size_before);
        if (!file.endsWith('-shm')) {
          expect(mtime).toBe(mtime_before);
          expect(bytes).toBe(bytes_before);
          continue;
        }
        const now = Buffer.from(bytes, 'base64');
        const then = Buffer.from(bytes_before, 'base64');
        const changed = [...now.keys()].filter((offset) => now[offset] !== then[offset]);
        expect(changed.every((offset) => offset >= 100 && offset < 120)).toBe(true);
      }
      expect((await native.snapshot(Infinity)).get(current.pid)?.birth).toBe(current.birth);
    } finally {
      db.close();
      await rm(root, { recursive: true, force: true });
    }
    await expect(inspect_legacy_agents(join(root, 'missing.sqlite'))).rejects.toThrow();
  },
);

test('retirement scope includes orphan session members, unjoined cgroups and units after leader exit', async () => {
  const orphan = { ...process, pid: 43, birth: '456' };
  const f = observation();
  const stable = new Map([[43, orphan]]);
  Object.assign(f, {
    processes: {
      stable,
      ...index_processes(stable),
      latest_sessions: index_processes(stable).sessions,
    },
  });
  f.details = async (pid) => ({
    pid,
    birth: '456',
    command: 'old tool',
    owner: 'uid:1000',
    age_seconds: 1,
  });
  f.journal = async () => ({
    version: 1,
    ticket_id: 'ticket',
    host: row.host!,
    boot: row.boot!,
    root: process,
    processes: [process],
    isolated: true,
    cgroup: { path: 'group', inode: 1n, root_device: 1n, root_inode: 1n },
    cgroup_joined: false,
  });
  const members = vi.fn(async () => [43]);
  f.members = members;
  const report = await plan_legacy_agents([{ ...row, supervision_record: 'journal' }], f);
  expect(report.would_stop.map((process) => process.pid)).toEqual([43]);
  expect(members).toHaveBeenCalledOnce();
  vi.mocked(inspect_legacy_agent_unit).mockResolvedValue([orphan]);
  const unit = await plan_legacy_agents(
    [{ ...row, unit: 'panels-worker-' + 'a'.repeat(32) + '.service' }],
    f,
  );
  expect(unit.would_stop.map((process) => process.pid)).toEqual([43]);
});
