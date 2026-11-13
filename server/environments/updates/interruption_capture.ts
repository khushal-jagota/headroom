/** A stopped helper records evidence only. It never initializes an application database. */
import { join } from 'node:path';
import { createReadAccess } from '../../core/database/index.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import {
  acquireRuntimeOwnership,
  canonical_database_path,
  ConversationRuntimeAlreadyOwnedError,
} from '../../core/locks/index.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { python_os_error_message } from '../../core/os_errors.ts';
import { is_os_error } from '../../core/system_errors.ts';
import { retained_run_is_unresolved } from '../../conversation/record/data/lifecycle.ts';
import {
  CAPTURE_VERSION,
  CaptureValidationError,
} from '../../conversation/deploy_interruptions/contracts.ts';
import type {
  CaptureManifest,
  CapturedTurn,
} from '../../conversation/deploy_interruptions/contracts.ts';
import {
  manifest_from_dict,
  manifest_root,
  write_manifest,
} from '../../conversation/deploy_interruptions/files.ts';
import { expand_path } from '../paths.ts';

const text = (value: unknown): string => (value === null ? 'None' : String(value));
export function read_unresolved_turns(
  sql: ReadSql,
  conversation_ids?: readonly string[],
): readonly CapturedTurn[] {
  const tables = new Set(
    sql
      .all<{ name: string }>("SELECT name FROM sqlite_master WHERE type='table'")
      .map((row) => row.name),
  );
  const current = tables.has('conversation_runtime_epoch')
    ? sql.get<{ epoch: unknown }>('SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1')
    : undefined;
  const legacy =
    current === undefined
      ? sql.get<{ runtime_epoch: unknown }>(
          'SELECT runtime_epoch FROM signal_publication_meta WHERE singleton=1',
        )
      : undefined;
  const epoch =
    current !== undefined
      ? text(current.epoch)
      : legacy !== undefined
        ? text(legacy.runtime_epoch)
        : null;
  const only =
    conversation_ids === undefined
      ? ''
      : `AND conversation_id IN (${conversation_ids.map(() => '?').join(',')}) `;
  const facts = sql.all<{
    conversation_id: unknown;
    turn_number: number;
    status: 'intent' | 'running';
  }>(
    "SELECT conversation_id,turn_number,status FROM conversation_lifecycle_facts WHERE kind='turn' AND status IN ('intent','running') " +
      only +
      'ORDER BY conversation_id,turn_number',
    conversation_ids ?? [],
  );
  const captured: CapturedTurn[] = [];
  for (const fact of facts) {
    const conversation_id = text(fact.conversation_id);
    if (retained_run_is_unresolved(sql, conversation_id)) continue;
    const occurrence = tables.has('conversation_turn_occurrences')
      ? sql.get<{ occurrence_id: unknown; runtime_epoch: unknown }>(
          'SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences WHERE conversation_id=? AND turn_number=?',
          [conversation_id, fact.turn_number],
        )
      : undefined;
    const occurrence_id = occurrence === undefined ? null : text(occurrence.occurrence_id);
    const runtime_epoch = occurrence === undefined ? epoch : text(occurrence.runtime_epoch);
    if (occurrence_id === null && runtime_epoch === null)
      throw new CaptureValidationError('unresolved legacy turn has no persisted runtime epoch');
    const turn = {
      conversation_id,
      turn_number: Math.trunc(fact.turn_number),
      occurrence_id,
      runtime_epoch,
      original_status: fact.status,
    };
    const ticket = sql.get<{
      id: unknown;
      title: unknown;
      worker_step_claim_revision: number;
      worker_step_claim: unknown;
    }>(
      'SELECT id,title,worker_step_claim_revision,worker_step_claim FROM tickets WHERE conversation_id=?',
      [conversation_id],
    );
    if (ticket !== undefined) {
      // Transitional raw projection. write_manifest validates the claim before publication.
      captured.push({
        ...turn,
        display_name: text(ticket.title),
        owner_kind: 'ticket',
        owner_id: text(ticket.id),
        claim_revision: Math.trunc(ticket.worker_step_claim_revision),
        claim: text(ticket.worker_step_claim) as CapturedTurn['claim'],
      });
      continue;
    }
    const historical = sql.get<{ owner_id: unknown; title: unknown }>(
      "SELECT h.owner_id,t.title FROM conversation_history_catalog h JOIN tickets t ON t.id=h.owner_id WHERE h.conversation_id=? AND h.owner_kind='ticket' LIMIT 1",
      [conversation_id],
    );
    if (historical !== undefined) {
      captured.push({
        ...turn,
        display_name: text(historical.title),
        owner_kind: 'ticket',
        owner_id: text(historical.owner_id),
        claim_revision: null,
        claim: null,
      });
      continue;
    }
    const owner = sql.get<{ owner_id: unknown; title: unknown }>(
      "SELECT h.owner_id,m.title FROM conversation_history_catalog h JOIN managers m ON m.id=h.owner_id WHERE h.conversation_id=? AND h.owner_kind='manager' LIMIT 1",
      [conversation_id],
    );
    const chief = sql.get(
      "SELECT 1 FROM agents WHERE agent_key='chief_of_staff' AND conversation_id=?",
      [conversation_id],
    );
    captured.push({
      ...turn,
      display_name:
        owner !== undefined
          ? text(owner.title)
          : chief !== undefined
            ? 'Chief of Staff'
            : 'Agent conversation',
      owner_kind: owner !== undefined ? 'manager' : chief !== undefined ? 'chief' : 'unknown',
      owner_id: owner !== undefined ? text(owner.owner_id) : chief !== undefined ? 'chief' : null,
      claim_revision: null,
      claim: null,
    });
  }
  return captured;
}

/** Diagnostic projection only. Keep native/core errors themselves unchanged. */
function failure_detail(error: unknown, canonical: string): string {
  if (error instanceof ConversationRuntimeAlreadyOwnedError)
    return `ConversationRuntimeAlreadyOwned: ${canonical}`;
  if (!(error instanceof Error)) return `Error: ${String(error)}`;
  if ('code' in error && typeof error.code === 'string') {
    const code = error.code.split('_').slice(0, 2).join('_');
    if (code.startsWith('SQLITE_')) {
      const kinds: Record<string, string> = {
        SQLITE_INTERNAL: 'InternalError',
        SQLITE_NOTFOUND: 'InternalError',
        SQLITE_NOMEM: 'MemoryError',
        SQLITE_TOOBIG: 'DataError',
        SQLITE_CONSTRAINT: 'IntegrityError',
        SQLITE_MISMATCH: 'IntegrityError',
        SQLITE_MISUSE: 'InterfaceError',
        SQLITE_RANGE: 'InterfaceError',
      };
      const operational = new Set([
        'ERROR',
        'PERM',
        'ABORT',
        'BUSY',
        'LOCKED',
        'READONLY',
        'INTERRUPT',
        'IOERR',
        'FULL',
        'CANTOPEN',
        'PROTOCOL',
        'EMPTY',
        'SCHEMA',
      ]);
      const kind =
        kinds[code] ?? (operational.has(code.slice(7)) ? 'OperationalError' : 'DatabaseError');
      return `${kind}: ${code === 'SQLITE_NOMEM' ? '' : error.message}`;
    }
    if (is_os_error(error)) {
      const kinds: Record<string, string> = {
        ENOENT: 'FileNotFoundError',
        EACCES: 'PermissionError',
        EPERM: 'PermissionError',
        EISDIR: 'IsADirectoryError',
        ENOTDIR: 'NotADirectoryError',
        EEXIST: 'FileExistsError',
      };
      return `${kinds[error.code] ?? 'OSError'}: ${python_os_error_message(error)}`;
    }
  }
  return `${error.name}: ${error.message}`;
}

export function capture_stopped_database(
  database: string,
  deployment_id: string,
  capture_id: string,
): CaptureManifest {
  const admitted = assert_test_database_path(database);
  // files.manifest_root expands user notation; admit its actual spelling separately.
  assert_test_database_path(expand_path(database));
  const captured_at = Math.trunc(Date.now() / 1000);
  const unavailable = (detail: string): CaptureManifest => ({
    deployment_id,
    capture_id,
    captured_at,
    state: 'unavailable',
    detail,
    turns: [],
    version: CAPTURE_VERSION,
  });
  const initial = unavailable('The interruption capture did not complete.');
  manifest_from_dict(initial);
  assert_test_database_path(join(manifest_root(database), deployment_id, capture_id + '.json'));
  write_manifest(database, initial);
  let result: CaptureManifest,
    canonical = database;
  try {
    canonical = canonical_database_path(admitted);
    const lease = acquireRuntimeOwnership(admitted);
    try {
      const reader = createReadAccess(canonical, { busyTimeoutMs: 0 });
      let turns: readonly CapturedTurn[];
      try {
        turns = reader.read((sql) => read_unresolved_turns(sql));
      } finally {
        reader.close();
      }
      result = {
        deployment_id,
        capture_id,
        captured_at,
        state: 'captured',
        detail: null,
        turns,
        version: CAPTURE_VERSION,
      };
      write_manifest(database, result);
      return result;
    } finally {
      lease.close();
    }
  } catch (error) {
    result = unavailable(
      Array.from('Interruption capture failed: ' + failure_detail(error, canonical))
        .slice(0, 8192)
        .join(''),
    );
  }
  write_manifest(database, result);
  return result;
}
