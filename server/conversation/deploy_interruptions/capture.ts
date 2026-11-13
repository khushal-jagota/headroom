/** Read-only capture under the caller's existing snapshot or write transaction. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { ValueError } from '../../core/value_error.ts';
import { retained_run_is_unresolved } from '../record/data/lifecycle.ts';
import type { CapturedTurn, OwnerKind } from './contracts.ts';
export function read_unresolved_turns(
  sql: ReadSql,
  options: { conversation_ids?: readonly string[] } = {},
): readonly CapturedTurn[] {
  const tables = new Set(
    sql
      .all<{ name: string }>("SELECT name FROM sqlite_master WHERE type='table'")
      .map((row) => row.name),
  );
  let epoch = tables.has('conversation_runtime_epoch')
    ? sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1')
        ?.epoch
    : undefined;
  if (epoch === undefined)
    epoch = sql.get<{ runtime_epoch: string }>(
      'SELECT runtime_epoch FROM signal_publication_meta WHERE singleton=1',
    )?.runtime_epoch;
  const only =
    options.conversation_ids === undefined
      ? ''
      : `AND conversation_id IN (${options.conversation_ids.map(() => '?').join(',')}) `;
  const facts = sql.all<{
    conversation_id: string;
    turn_number: number;
    status: 'running' | 'intent';
  }>(
    "SELECT conversation_id,turn_number,status FROM conversation_lifecycle_facts WHERE kind='turn' AND status IN ('intent','running') " +
      only +
      'ORDER BY conversation_id,turn_number',
    options.conversation_ids ?? [],
  );
  const captured: CapturedTurn[] = [];
  for (const fact of facts) {
    const conversation_id = fact.conversation_id;
    if (retained_run_is_unresolved(sql, conversation_id)) continue;
    const occurrence = tables.has('conversation_turn_occurrences')
      ? sql.get<{ occurrence_id: string; runtime_epoch: string }>(
          'SELECT occurrence_id,runtime_epoch FROM conversation_turn_occurrences WHERE conversation_id=? AND turn_number=?',
          [conversation_id, fact.turn_number],
        )
      : undefined;
    const occurrence_id = occurrence?.occurrence_id ?? null,
      runtime_epoch = occurrence?.runtime_epoch ?? epoch ?? null;
    if (occurrence_id === null && runtime_epoch === null)
      throw new ValueError('unresolved legacy turn has no persisted runtime epoch');
    const ticket = sql.get<{
      id: string;
      title: string;
      worker_step_claim_revision: number;
      worker_step_claim: 'none' | 'out' | 'errored';
    }>(
      'SELECT id,title,worker_step_claim_revision,worker_step_claim FROM tickets WHERE conversation_id=?',
      [conversation_id],
    );
    let display_name: string,
      owner_kind: OwnerKind,
      owner_id: string | null,
      claim_revision: number | null = null,
      claim: CapturedTurn['claim'] = null;
    if (ticket !== undefined) {
      display_name = ticket.title;
      owner_kind = 'ticket';
      owner_id = ticket.id;
      claim_revision = ticket.worker_step_claim_revision;
      claim = ticket.worker_step_claim;
    } else {
      const historical = sql.get<{ owner_id: string; title: string }>(
        "SELECT h.owner_id,t.title FROM conversation_history_catalog h JOIN tickets t ON t.id=h.owner_id WHERE h.conversation_id=? AND h.owner_kind='ticket' LIMIT 1",
        [conversation_id],
      );
      if (historical !== undefined) {
        display_name = historical.title;
        owner_kind = 'ticket';
        owner_id = historical.owner_id;
      } else {
        const owner = sql.get<{ owner_id: string; title: string }>(
          "SELECT h.owner_id,m.title FROM conversation_history_catalog h JOIN managers m ON m.id=h.owner_id WHERE h.conversation_id=? AND h.owner_kind='manager' LIMIT 1",
          [conversation_id],
        );
        const chief = sql.get(
          "SELECT 1 FROM agents WHERE agent_key='chief_of_staff' AND conversation_id=?",
          [conversation_id],
        );
        display_name =
          owner?.title ?? (chief !== undefined ? 'Chief of Staff' : 'Agent conversation');
        owner_kind = owner !== undefined ? 'manager' : chief !== undefined ? 'chief' : 'unknown';
        owner_id = owner?.owner_id ?? (chief !== undefined ? 'chief' : null);
      }
    }
    captured.push({
      conversation_id,
      turn_number: fact.turn_number,
      occurrence_id,
      runtime_epoch,
      original_status: fact.status,
      display_name,
      owner_kind,
      owner_id,
      claim_revision,
      claim,
    });
  }
  return captured;
}
