import { inspect_legacy_agent_unit } from './legacy_agent_units.ts';
import type {
  LegacyProcessDetails,
  LegacyAgentReport,
  LegacyAgentObservation,
} from './legacy_agent_contracts.ts';
import { legacy_direct_identity_matches } from './logic/legacy_agent_identity.ts';
import { WorkerCgroups } from '../platform_runtime/linux/worker_cgroups.ts';
/** Read-only inventory of the one-release startup retirement, never a cleanup operation. */
import BetterSqlite3 from 'better-sqlite3';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { WorkerProcess } from '../worker_supervision/contracts.ts';
import {
  LinuxWorkerProcesses,
  parse_linux_worker_process,
} from '../platform_runtime/linux/worker_processes.ts';
import { monotonic_seconds } from '../lifecycle/configuration.ts';
import { capture_observation } from '../worker_supervision/observation.ts';
import {
  alive,
  retirement_process_scope,
  validate_pending_shape,
} from '../worker_supervision/logic/ownership.ts';
import { read as read_record } from '../worker_supervision/records.ts';
import type { LocalChildCustodyRow } from './local_child_custody_contracts.ts';

export async function plan_legacy_agents(
  rows: readonly LocalChildCustodyRow[],
  observation: LegacyAgentObservation,
): Promise<LegacyAgentReport> {
  const would_stop: (LegacyProcessDetails & { record: string })[] = [];
  const unproven: { record: string; reason: string }[] = [];
  for (const row of rows) {
    if (row.state === 'retired') continue;
    const source = `conversation_child_custody:${row.conversation_id}:${row.generation}`;
    try {
      if (row.host !== observation.host.host || row.boot !== observation.host.boot)
        throw new Error('Record belongs to another host or boot');
      let candidates: WorkerProcess[];
      if (row.supervision_record !== null) {
        const journal = await observation.journal(row.supervision_record);
        validate_pending_shape(journal);
        if (
          journal.host !== row.host ||
          journal.boot !== row.boot ||
          (journal.conversation_id != null && journal.conversation_id !== row.conversation_id)
        )
          throw new Error('Worker journal does not match the custody record');
        if (journal.launchd) throw new Error('Linux dry run cannot inspect a launchd job');
        if (
          row.state === 'started' &&
          (journal.root?.pid !== row.pid || journal.root.birth !== row.birth)
        )
          throw new Error('Worker journal root does not match the custody incarnation');
        if (journal.state === 'complete') candidates = [];
        else {
          candidates = retirement_process_scope(journal, observation.processes, true).filter(
            (process) => alive(process, observation.processes.stable),
          );
          if (journal.cgroup) {
            if (!observation.members)
              throw new Error('Worker cgroup member inventory is unavailable');
            for (const pid of await observation.members(journal)) {
              const process = observation.processes.stable.get(pid);
              if (!process) throw new Error('Worker cgroup member changed during inspection');
              if (!candidates.some((candidate) => candidate.pid === pid)) candidates.push(process);
            }
          }
        }
      } else if (row.unit !== null) {
        candidates = [...(await inspect_legacy_agent_unit(row.unit, observation.processes.stable))];
      } else {
        if (
          row.state !== 'started' ||
          row.pid === null ||
          !legacy_direct_identity_matches(
            row,
            observation.host,
            observation.processes.stable.get(row.pid),
          )
        )
          throw new Error('No live process matches the recorded incarnation');
        candidates = [observation.processes.stable.get(row.pid)!];
      }
      for (const process of candidates) {
        const details = await observation.details(process.pid);
        if (details.birth !== process.birth)
          throw new Error('Process incarnation changed during inspection');
        would_stop.push({
          ...details,
          record:
            row.supervision_record === null ? source : `${source} -> ${row.supervision_record}`,
        });
      }
      if (!candidates.length)
        unproven.push({ record: source, reason: 'No live process is proven by this record' });
    } catch (error) {
      unproven.push({
        record: source,
        reason: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return { would_stop, unproven };
}

export async function inspect_legacy_agents(database_path: string): Promise<LegacyAgentReport> {
  if (process.platform !== 'linux')
    throw new Error('Legacy agent dry run currently requires Linux');
  // Deliberately bypass createDatabase: it initializes skills, schema and projections.
  const scratch = await mkdtemp(join(tmpdir(), 'panels-legacy-dry-run-'));
  const copy = join(scratch, 'database.sqlite');
  const source = async (suffix: string): Promise<Buffer | null> => {
    try {
      return await readFile(database_path + suffix);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT' && suffix) return null;
      throw error;
    }
  };
  let database: BetterSqlite3.Database | undefined;
  try {
    const suffixes = ['', '-wal'];
    const before = await Promise.all(suffixes.map(source));
    for (let index = 0; index < suffixes.length; index++)
      if (before[index] !== null)
        await writeFile(copy + suffixes[index], before[index]!, { mode: 0o600 });
    const after = await Promise.all(suffixes.map(source));
    if (
      before.some((bytes, index) =>
        bytes === null
          ? after[index] !== null
          : after[index] === null || !bytes.equals(after[index]!),
      )
    )
      throw new Error('Database or WAL changed during the read-only snapshot. Retry the dry run.');
    database = new BetterSqlite3(copy, { readonly: true, fileMustExist: true });

    database.pragma('query_only = ON');
    const rows = database
      .prepare("SELECT * FROM conversation_child_custody WHERE state!='retired'")
      .all() as LocalChildCustodyRow[];
    const native = new LinuxWorkerProcesses();
    const deadline = monotonic_seconds() + 30;
    const host = await native.host_boot(deadline);
    const processes = await capture_observation({ host, primitives: native }, deadline);
    return await plan_legacy_agents(rows, {
      host,
      processes,
      journal: read_record,
      members: (record) => new WorkerCgroups().members(record.cgroup!),
      details: async (pid) => {
        const root = `/proc/${pid}`;
        const before = parse_linux_worker_process(pid, await readFile(`${root}/stat`, 'utf8'));
        const command = (await readFile(`${root}/cmdline`, 'utf8')).replace(/\0/g, ' ').trim();
        const status = await readFile(`${root}/status`, 'utf8');
        const uid = /^Uid:\s+(\d+)/m.exec(status)?.[1];
        if (!uid || !command) throw new Error('Process command or owner is unavailable');
        const after = parse_linux_worker_process(pid, await readFile(`${root}/stat`, 'utf8'));
        if (before.birth !== after.birth)
          throw new Error('Process incarnation changed during inspection');
        const uptime = Number((await readFile('/proc/uptime', 'utf8')).split(' ')[0]);
        // Linux /proc stat exposes starttime in USER_HZ (100 ticks per second).
        return {
          pid,
          birth: before.birth,
          command,
          owner: `uid:${uid}`,
          age_seconds: Math.max(0, Math.floor(uptime - Number(before.birth) / 100)),
        };
      },
    });
  } finally {
    database?.close();
    await rm(scratch, { recursive: true, force: true });
  }
}
