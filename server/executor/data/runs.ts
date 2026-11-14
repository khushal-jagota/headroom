import { counter } from '../logic/counters.ts';
/** Durable run and exact child-assignment custody. All writes use the invocation door. */
import { BackendSpawnFailed, RetainedBackendRun } from '../../conversation/backends/contracts.ts';
import type { ChildReference } from '../../conversation/backends/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { Database, ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { execution_unavailable_reason } from '../../tickets/locale_configuration.ts';
import { current_incarnation } from '../vps_presence_store.ts';
import { ExecutorValidationError } from '../protocol.ts';
import type { WireValue } from '../contracts.ts';
import {
  open_child_assignment,
  prepare_command_in_transaction,
  record_prompt_outcome_in_transaction,
} from './child_custody.ts';

export class ExecutorStore {
  readonly database: Database;
  constructor(database: Database) {
    this.database = database;
  }
  retained_runs(): readonly RetainedBackendRun[] {
    return this.database.read((sql) =>
      sql
        .all<{
          executor_incarnation: string;
          child_id: string;
          conversation_id: string;
          turn_number: string;
          delivery_uncertain: number;
          stop_state: string;
          current_incarnation: string | null;
        }>(
          'SELECT r.executor_incarnation,r.child_id,r.conversation_id,CAST(r.turn_number AS TEXT) AS turn_number,r.delivery_uncertain,r.stop_state,p.executor_incarnation AS current_incarnation FROM mac_runs AS r LEFT JOIN mac_executor_presence AS p ON p.singleton=1 ORDER BY r.rowid',
        )
        .map(
          (row) =>
            new RetainedBackendRun({
              reference: {
                executor_incarnation: row.executor_incarnation,
                child_id: row.child_id,
                conversation_id: row.conversation_id,
              },
              turn: {
                conversation_id: row.conversation_id,
                turn_number: counter(row.turn_number),
              },
              delivery_uncertain: Boolean(row.delivery_uncertain),
              retirement_confirmed: row.stop_state === 'closed',
              stop_requested: row.stop_state === 'stopping',
              incarnation_current: row.executor_incarnation === row.current_incarnation,
            }),
        ),
    );
  }
  unresolved_runs_of_replaced_incarnations(): readonly ChildReference[] {
    return this.database.read((sql) =>
      sql.all<ChildReference>(
        "SELECT r.executor_incarnation,r.child_id,r.conversation_id FROM mac_runs AS r LEFT JOIN mac_executor_presence AS p ON p.singleton=1 WHERE r.stop_state<>'closed' AND r.executor_incarnation IS NOT p.executor_incarnation ORDER BY r.rowid",
      ),
    );
  }
  prepare_command(command: WireValue<'ChildCommand'>): void {
    this.database.write((tx) => prepare_command_in_transaction(tx, command));
  }
  readonly prepare_command_in_transaction = prepare_command_in_transaction;
  record_prompt_outcome(
    command: WireValue<'ChildCommand'>,
    outcome: WireValue<'CommandOutcome'>,
  ): void {
    this.database.write((tx) => record_prompt_outcome_in_transaction(tx, command, outcome));
  }
  readonly record_prompt_outcome_in_transaction = record_prompt_outcome_in_transaction;
  register_child(
    child: ChildReference,
    principal_kind: string,
    principal_id: string,
    now_ms: number,
  ): void {
    this.database.write((tx) => {
      const current = current_incarnation(tx);
      if (current !== child.executor_incarnation)
        throw new BackendSpawnFailed(
          current !== null
            ? 'The Mac executor process changed before this child was assigned'
            : execution_unavailable_reason(tx, 'macos'),
        );
      const existing = tx.get<{
        conversation_id: string;
        principal_kind: string;
        principal_id: string;
      }>(
        'SELECT conversation_id,principal_kind,principal_id FROM executor_child_assignments WHERE executor_incarnation=? AND child_id=?',
        [child.executor_incarnation, child.child_id],
      );
      if (existing) {
        if (
          existing.conversation_id !== child.conversation_id ||
          existing.principal_kind !== principal_kind ||
          existing.principal_id !== principal_id
        )
          throw new PlannerError('validation', 'Child assignment identity conflicts');
        return;
      }
      const retained = tx.get<{ stop_state: string }>(
        'SELECT stop_state FROM mac_runs WHERE conversation_id=?',
        [child.conversation_id],
      );
      if (retained && retained.stop_state !== 'closed')
        throw new PlannerError(
          'validation',
          'An unresolved Mac run already owns this conversation',
        );
      const turn = tx.get<{ turn_number: string }>(
        "SELECT CAST(COALESCE(MAX(turn_number),0) AS TEXT) AS turn_number FROM conversation_lifecycle_facts WHERE conversation_id=? AND kind='turn' AND status<>'intent'",
        [child.conversation_id],
      )!;
      tx.run(
        "INSERT INTO mac_runs VALUES(?,?,?,?,?,?,0,'open') ON CONFLICT(conversation_id) DO UPDATE SET executor_incarnation=excluded.executor_incarnation,child_id=excluded.child_id,principal_kind=excluded.principal_kind,principal_id=excluded.principal_id,turn_number=excluded.turn_number,delivery_uncertain=0,stop_state='open'",
        [
          child.conversation_id,
          child.executor_incarnation,
          child.child_id,
          principal_kind,
          principal_id,
          counter(turn.turn_number),
        ],
      );
      tx.run('INSERT INTO executor_child_assignments VALUES (?,?,?,?,?,?,NULL)', [
        child.executor_incarnation,
        child.child_id,
        child.conversation_id,
        principal_kind,
        principal_id,
        now_ms,
      ]);
    });
  }
  has_closed_child_assignment(child: ChildReference): boolean {
    return this.database.read((sql) => has_closed_child_assignment(sql, child));
  }
  child_assignment(child: ChildReference): readonly [string, string] | null {
    return this.database.read((sql) => open_child_assignment(sql, child));
  }
  readonly open_child_assignment = open_child_assignment;
  close_child(child: ChildReference, now_ms: number): void {
    this.database.write((tx) => close_child_in_transaction(tx, child, now_ms));
  }
  readonly close_child_in_transaction = close_child_in_transaction;
}
function has_closed_child_assignment(sql: ReadSql, child: ChildReference): boolean {
  return (
    sql.get(
      'SELECT 1 FROM executor_child_assignments WHERE executor_incarnation=? AND child_id=? AND conversation_id=? AND closed_at_ms IS NOT NULL',
      [child.executor_incarnation, child.child_id, child.conversation_id],
    ) !== undefined
  );
}
export function close_child_in_transaction(
  tx: TransactionSql,
  child: ChildReference,
  now_ms: number,
): void {
  if (has_closed_child_assignment(tx, child)) return;
  if (
    !tx.get(
      'SELECT stop_state FROM mac_runs WHERE conversation_id=? AND executor_incarnation=? AND child_id=?',
      [child.conversation_id, child.executor_incarnation, child.child_id],
    )
  )
    throw new ExecutorValidationError('Mac run does not match this child');
  tx.run(
    "UPDATE mac_runs SET stop_state='closed' WHERE conversation_id=? AND executor_incarnation=? AND child_id=?",
    [child.conversation_id, child.executor_incarnation, child.child_id],
  );
  tx.run(
    'UPDATE executor_child_assignments SET closed_at_ms=? WHERE executor_incarnation=? AND child_id=? AND conversation_id=?',
    [now_ms, child.executor_incarnation, child.child_id, child.conversation_id],
  );
}
