import type { TransactionSql } from '../core/database/contracts.ts';
import type { Principal } from '../core/contracts.ts';
interface Wake {
  manager_id: string;
  ticket_id: string;
  source_kind: string;
  source_revision: number;
  summary: string;
  now: number;
  target?: Principal;
}
function create_wake(sql: TransactionSql, w: Wake): boolean {
  const target = w.target ?? { kind: 'manager', id: w.manager_id };
  return (
    sql.run(
      'INSERT OR IGNORE INTO manager_wakes(manager_id,ticket_id,source_kind,source_revision,summary,created_at,target_kind,target_id,source_conversation_id,measure_source_id,source_episode,source_responsibility_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
      [
        w.manager_id,
        w.ticket_id,
        w.source_kind,
        w.source_revision,
        w.summary,
        w.now,
        target.kind,
        target.id,
        null,
        null,
        null,
        null,
      ],
    ).changes === 1
  );
}
export function create_proposal_wake(
  sql: TransactionSql,
  w: {
    manager_id: string;
    ticket_id: string;
    proposal_revision: number;
    ticket_title: string;
    proposal_field: string;
    now: number;
    target?: Principal;
  },
): boolean {
  return create_wake(sql, {
    ...w,
    source_kind: 'proposal',
    source_revision: w.proposal_revision,
    summary: `Ticket \`${w.ticket_id}\` (${w.ticket_title}) filed a proposal for \`${w.proposal_field}\`.`,
  });
}
export function create_worker_error_wake(
  sql: TransactionSql,
  w: {
    target: Principal;
    ticket_id: string;
    claim_revision: number;
    ticket_title: string;
    error_reason: string;
    now: number;
  },
): boolean {
  return create_wake(sql, {
    ...w,
    manager_id: w.target.kind === 'manager' ? w.target.id : '',
    source_kind: 'worker_error',
    source_revision: w.claim_revision,
    summary: `Ticket \`${w.ticket_id}\` (${w.ticket_title}) entered the explicit worker-error state: ${w.error_reason}.`,
  });
}
