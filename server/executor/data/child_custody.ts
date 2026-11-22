import { counter } from '../logic/counters.ts';
/** The mailbox's three run-custody participants. The caller owns their transaction. */
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { ChildReference } from '../../conversation/backends/contracts.ts';
import type { WireValue } from '../contracts.ts';
import { ExecutorValidationError } from '../protocol.ts';
import { canonical_principal } from '../../agent_identity/data.ts';
import { PlannerError } from '../../core/contracts.ts';

export function open_child_assignment(
  sql: ReadSql,
  child: ChildReference,
): readonly [string, string] | null {
  const row = sql.get<{ principal_kind: string; principal_id: string }>(
    'SELECT principal_kind,principal_id FROM executor_child_assignments WHERE executor_incarnation=? ' +
      'AND child_id=? AND conversation_id=? AND closed_at_ms IS NULL',
    [child.executor_incarnation, child.child_id, child.conversation_id],
  );
  return row ? [row.principal_kind, row.principal_id] : null;
}
/**
 * The owner that an agent request acts for. A local agent's launch token is revoked when its
 * owner moves to a new conversation. So a child acts for its owner only while the child's
 * conversation is still that owner's current conversation.
 */
export function current_agent_owner(
  sql: ReadSql,
  child: ChildReference,
): { readonly kind: string; readonly id: string } {
  const assignment = open_child_assignment(sql, child);
  if (assignment === null)
    throw new PlannerError('agent_forbidden', 'child has no open assignment');
  const [kind, id] = assignment;
  const current = canonical_principal(sql, child.conversation_id);
  const still_current =
    kind === 'conversation'
      ? current === null && id === child.conversation_id
      : current?.kind === kind && current.id === id;
  if (!still_current)
    throw new PlannerError(
      'agent_forbidden',
      "this agent's conversation is no longer its owner's current conversation",
    );
  return { kind, id };
}
export function prepare_command_in_transaction(
  tx: TransactionSql,
  command: WireValue<'ChildCommand'>,
): void {
  const child = command.child;
  const row = tx.get<{ stop_state: string; turn_number: string }>(
    'SELECT stop_state,CAST(turn_number AS TEXT) AS turn_number FROM mac_runs WHERE conversation_id=? ' +
      'AND executor_incarnation=? AND child_id=?',
    [child.conversation_id, child.executor_incarnation, child.child_id],
  );
  if (!row || row.stop_state === 'closed')
    throw new ExecutorValidationError('Mac run no longer owns this conversation');
  if (command.operation === 'start' || command.operation === 'write_prompt') {
    if (
      row.stop_state !== 'open' ||
      (command.turn !== null && command.turn.turn_number < counter(row.turn_number))
    )
      throw new ExecutorValidationError('Mac run cannot accept a stale or stopped delivery');
    tx.run('UPDATE mac_runs SET delivery_uncertain=1,turn_number=? WHERE conversation_id=?', [
      command.turn === null ? counter(row.turn_number) : command.turn.turn_number,
      child.conversation_id,
    ]);
  } else if (command.operation === 'stop') {
    tx.run(
      "UPDATE mac_runs SET stop_state='stopping' WHERE conversation_id=? AND stop_state='open'",
      [child.conversation_id],
    );
  }
}
export function record_prompt_outcome_in_transaction(
  tx: TransactionSql,
  command: WireValue<'ChildCommand'>,
  outcome: WireValue<'CommandOutcome'>,
): void {
  if (command.operation !== 'write_prompt' || outcome.disposition === 'uncertain') return;
  const child = command.child;
  tx.run(
    'UPDATE mac_runs SET delivery_uncertain=0 WHERE conversation_id=? AND executor_incarnation=? ' +
      "AND child_id=? AND turn_number=? AND stop_state='open'",
    [
      child.conversation_id,
      child.executor_incarnation,
      child.child_id,
      command.turn === null ? 0 : command.turn.turn_number,
    ],
  );
}
