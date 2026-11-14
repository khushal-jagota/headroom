/** Read-only capture under the caller's existing snapshot or write transaction. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { retained_run_is_unresolved } from '../record/data/lifecycle.ts';
import { CaptureValidationError } from './contracts.ts';
import type { CapturedTurn } from './contracts.ts';

export function read_unresolved_turns(
  sql: ReadSql,
  options: { conversation_ids?: readonly string[] } = {},
): readonly CapturedTurn[] {
  const { conversation_ids } = options;
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
      ? String(current.epoch)
      : legacy !== undefined
        ? String(legacy.runtime_epoch)
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
    const conversation_id = String(fact.conversation_id);
    if (retained_run_is_unresolved(sql, conversation_id)) continue;
    const occurrence = tables.has('conversation_turn_occurrences')
      ? sql.get<{ occurrence_id: unknown; runtime_epoch: unknown }>(
          'SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences WHERE conversation_id=? AND turn_number=?',
          [conversation_id, fact.turn_number],
        )
      : undefined;
    const occurrence_id = occurrence === undefined ? null : String(occurrence.occurrence_id);
    const runtime_epoch = occurrence === undefined ? epoch : String(occurrence.runtime_epoch);
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
      captured.push({
        ...turn,
        display_name: String(ticket.title),
        owner_kind: 'ticket',
        owner_id: String(ticket.id),
        claim_revision: Math.trunc(ticket.worker_step_claim_revision),
        claim: String(ticket.worker_step_claim),
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
        display_name: String(historical.title),
        owner_kind: 'ticket',
        owner_id: String(historical.owner_id),
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
          ? String(owner.title)
          : chief !== undefined
            ? 'Chief of Staff'
            : 'Agent conversation',
      owner_kind: owner !== undefined ? 'manager' : chief !== undefined ? 'chief' : 'unknown',
      owner_id: owner !== undefined ? String(owner.owner_id) : chief !== undefined ? 'chief' : null,
      claim_revision: null,
      claim: null,
    });
  }
  return captured;
}
