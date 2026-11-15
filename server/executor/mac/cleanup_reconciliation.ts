/** Per-source observations preserve Ticket fences without granting signal authority. */
import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { is_record } from '../../core/records.ts';
import { INSPECTION_SECONDS } from '../../worker_supervision/config.ts';
import { cleanup_uncertainties, failure_message } from '../../worker_supervision/inspection.ts';
import * as records from '../../worker_supervision/records.ts';
import { same_reference } from '../child_reference.ts';
import { decode_json } from '../protocol.ts';
import type { ChildReference, WorkerCleanupUncertainty } from '../wire.generated.ts';
import type { ExecutorPaths } from './configuration.ts';
import type {
  CleanupSnapshotOptions,
  CleanupReconciliationDependencies,
  CleanupSource,
  ExecutorCleanupSnapshot,
  ReadCleanupCommands,
} from './cleanup_contracts.ts';
import { read_record } from './runtime/records.ts';
import { read_canonical_body } from './mailbox/poll.ts';
import type { AsyncCommandJournal } from './storage/journal_contracts.ts';

import { LEGACY_CLEANUP_RECEIPT, legacy_targets } from './legacy_worker_cleanup.ts';
export { LEGACY_CLEANUP_RECEIPT } from './legacy_worker_cleanup.ts';
/** Borrow the journal cursor. Preserve stored field presence before wire defaults. */
export function read_cleanup_commands(
  journal: Pick<AsyncCommandJournal, 'cleanup_evidence'>,
): ReadCleanupCommands {
  return async function* () {
    const cursor = await journal.cleanup_evidence();
    try {
      for await (const row of cursor) {
        try {
          const command_json = await read_canonical_body(row.command);
          const result_json = row.result === null ? null : await read_canonical_body(row.result);
          yield { command_json, result_json };
        } finally {
          await Promise.all([row.command.close(), row.result?.close()]);
        }
      }
    } finally {
      await cursor.close();
    }
  };
}

async function record_paths(run: string): Promise<string[]> {
  const root = join(run, 'worker-processes');
  try {
    return (await readdir(root))
      .filter((name) => name.endsWith('.json'))
      .sort()
      .map((name) => join(root, name));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
}
async function record_source(path: string): Promise<CleanupSource | null> {
  try {
    const record = await records.read(path);
    if (
      record.source_incarnation &&
      record.source_child_id &&
      record.conversation_id &&
      record.ticket_id
    )
      return {
        source: {
          executor_incarnation: record.source_incarnation,
          child_id: record.source_child_id,
          conversation_id: record.conversation_id,
        },
        ticket_id: record.ticket_id,
      };
  } catch {
    // Invalid records remain global diagnostic evidence, never a guessed attribution.
  }
  return null;
}
function assign_source(sources: CleanupSource[], entry: CleanupSource): void {
  const index = sources.findIndex((existing) => same_reference(existing.source, entry.source));
  if (index === -1) sources.push(entry);
  else sources[index] = entry;
}
async function sources(run: string, read_commands: ReadCleanupCommands): Promise<CleanupSource[]> {
  const found: CleanupSource[] = [];
  for await (const row of read_commands()) {
    if (!row.command_json.length) continue;
    const command = decode_json(row.command_json, 'MailboxCommand').child_command;
    if (command?.arguments.kind !== 'start') continue;
    const ticket_id = new Map(
      command.arguments.resolved_start.role_materials?.identity_environment_variables ?? [],
    ).get('PLAN_TICKET_ID');
    if (ticket_id) assign_source(found, { source: command.child, ticket_id });
  }
  for (const path of await record_paths(run)) {
    const entry = await record_source(path);
    if (entry) assign_source(found, entry);
  }
  return found;
}
async function modern_sources(read_commands: ReadCleanupCommands): Promise<ChildReference[]> {
  const found: ChildReference[] = [];
  for await (const row of read_commands()) {
    if (!row.command_json.length) continue;
    const raw: unknown = JSON.parse(
      typeof row.command_json === 'string'
        ? row.command_json
        : new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(row.command_json),
    );
    if (!is_record(raw)) throw new Error('Worker command evidence is not an object');
    const child = raw.child_command;
    if (
      is_record(child) &&
      child.operation === 'start' &&
      is_record(child.arguments) &&
      child.arguments.supervision_protocol === 24
    ) {
      const command = decode_json(row.command_json, 'MailboxCommand').child_command;
      if (command) found.push(command.child);
    }
  }
  return found;
}
async function stopped_sources(read_commands: ReadCleanupCommands): Promise<ChildReference[]> {
  const found: ChildReference[] = [];
  for await (const row of read_commands()) {
    if (!row.command_json.length || row.result_json === null) continue;
    const command = decode_json(row.command_json, 'MailboxCommand').child_command;
    const outcome = decode_json(row.result_json, 'CommandResult').child_outcome;
    if (
      command &&
      outcome &&
      ((command.operation === 'stop' && outcome.disposition === 'succeeded') ||
        (command.operation === 'start' && outcome.disposition === 'refused'))
    )
      found.push(command.child);
  }
  return found;
}
/** This observer reads a historical receipt. It never performs the legacy migration. */
export function legacy_notice(
  run: string,
  dependencies: CleanupReconciliationDependencies,
): Promise<string | null> {
  return dependencies.runtime.operation(async () => {
    const receipt = await read_record(dependencies.files, join(run, LEGACY_CLEANUP_RECEIPT));
    if (receipt === null) return null;
    if (receipt.version !== 1 || receipt.attempted !== true)
      return 'The legacy cleanup receipt is unreadable';
    const targets = legacy_targets(receipt.targets);
    const deadline = dependencies.runtime.clock() + INSPECTION_SECONDS;
    const unresolved: number[] = [];
    for (const target of targets) {
      try {
        if (await dependencies.inspect_legacy(target, deadline)) unresolved.push(target.process_id);
      } catch {
        unresolved.push(target.process_id);
      }
    }
    return unresolved.length
      ? `Unassigned legacy Worker processes need cleanup: PID ${unresolved.join(', ')}`
      : null;
  });
}

export function snapshot_cleanup(
  paths: Pick<ExecutorPaths, 'run'>,
  dependencies: CleanupReconciliationDependencies,
  options: CleanupSnapshotOptions = {},
): Promise<ExecutorCleanupSnapshot> {
  return dependencies.runtime.operation(async () => {
    const notices: string[] = [];
    let source_evidence: CleanupSource[];
    try {
      source_evidence = await sources(paths.run, dependencies.read_commands);
      if (options.ticket_id !== undefined)
        source_evidence = source_evidence.filter((entry) => entry.ticket_id === options.ticket_id);
    } catch (error) {
      source_evidence = [];
      notices.push(`Worker source evidence is unavailable: ${failure_message(error)}`);
    }
    const observed_records: ChildReference[] = [];
    for (const path of await record_paths(paths.run)) {
      const entry = await record_source(path);
      if (entry) observed_records.push(entry.source);
    }
    const healthy = [...(options.healthy_sources ?? []), ...(options.preparing_sources ?? [])];
    const found = new Map<string, WorkerCleanupUncertainty>();
    function add(ticket_id: string, conversation_id: string, reason: string): void {
      found.set(JSON.stringify([ticket_id, conversation_id]), {
        ticket_id,
        conversation_id,
        reason,
      });
    }
    for (const uncertain of await cleanup_uncertainties(join(paths.run, 'worker-processes'), {
      runtime: dependencies.runtime,
      ticket_id: options.ticket_id,
      revoke_pending: options.revoke_pending,
      healthy_sources: new Set(
        healthy.map((source) => JSON.stringify([source.executor_incarnation, source.child_id])),
      ),
    })) {
      if (uncertain.ticket_id !== null && uncertain.conversation_id !== null)
        add(uncertain.ticket_id, uncertain.conversation_id, uncertain.reason);
      else if (uncertain.ticket_id !== null) {
        const candidates = source_evidence.filter(
          (entry) => entry.ticket_id === uncertain.ticket_id,
        );
        for (const candidate of candidates)
          add(uncertain.ticket_id, candidate.source.conversation_id, uncertain.reason);
        if (!candidates.length) {
          add(uncertain.ticket_id, '', uncertain.reason);
          notices.push(uncertain.reason);
        }
      } else notices.push(uncertain.reason);
    }
    let legacy_attempted = false;
    let modern: readonly ChildReference[];
    try {
      const receipt = await read_record(
        dependencies.files,
        join(paths.run, LEGACY_CLEANUP_RECEIPT),
      );
      legacy_attempted = receipt?.version === 1 && receipt.attempted === true;
      const notice =
        options.ticket_id === undefined ? await legacy_notice(paths.run, dependencies) : null;
      if (notice !== null) notices.push(notice);
      modern = await modern_sources(dependencies.read_commands);
    } catch (error) {
      notices.push(failure_message(error));
      modern = source_evidence.map((entry) => entry.source);
    }
    let stopped: readonly ChildReference[];
    try {
      stopped = await stopped_sources(dependencies.read_commands);
    } catch (error) {
      stopped = [];
      notices.push(`Worker stop evidence is unavailable: ${failure_message(error)}`);
    }
    for (const { source, ticket_id } of source_evidence) {
      if (
        healthy.some((current) => same_reference(current, source)) ||
        observed_records.some((current) => same_reference(current, source)) ||
        stopped.some((current) => same_reference(current, source)) ||
        (legacy_attempted && !modern.some((current) => same_reference(current, source)))
      )
        continue;
      add(
        ticket_id,
        source.conversation_id,
        'Exact Worker cleanup evidence is missing. Recheck after source evidence is restored.',
      );
    }
    return {
      uncertainties: [...found.values()].sort((left, right) =>
        left.ticket_id < right.ticket_id
          ? -1
          : left.ticket_id > right.ticket_id
            ? 1
            : left.conversation_id < right.conversation_id
              ? -1
              : left.conversation_id > right.conversation_id
                ? 1
                : 0,
      ),
      sources: source_evidence,
      notice: notices.length ? [...new Set(notices)].join(' ') : null,
    };
  });
}
