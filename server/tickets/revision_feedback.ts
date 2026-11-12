/** One-use feedback from the Ticket's current rejected stage. */
import type { Principal } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
export interface RevisionFeedbackItem {
  sender: Principal;
  message: string;
}
export class PendingRevisionFeedback {
  readonly ticket_id: string;
  readonly stage: string;
  readonly items: readonly RevisionFeedbackItem[];
  readonly revision: number;
  constructor(values: {
    ticket_id: string;
    stage: string;
    items: readonly RevisionFeedbackItem[];
    revision: number;
  }) {
    this.ticket_id = values.ticket_id;
    this.stage = values.stage;
    this.items = values.items;
    this.revision = values.revision;
  }
  get text(): string {
    return this.items
      .map(
        (item) =>
          `Revision feedback from ${item.sender.kind} ${item.sender.id} for stage ${this.stage}:\n${item.message}`,
      )
      .join('\n\n');
  }
}
export function snapshot(sql: ReadSql, ticket_id: string): PendingRevisionFeedback | null {
  const row = sql.get<{
    stage: string;
    feedback_json: string;
    revision: number;
  }>(
    'SELECT f.stage, f.feedback_json, f.revision ' +
      'FROM ticket_revision_feedback f JOIN tickets t ON t.id=f.ticket_id ' +
      'WHERE f.ticket_id=? AND f.stage=t.stage',
    [ticket_id],
  );
  if (!row) return null;
  const values = JSON.parse(row.feedback_json) as {
    sender_kind: Principal['kind'];
    sender_id: string;
    message: string;
  }[];
  return new PendingRevisionFeedback({
    ticket_id,
    stage: row.stage,
    revision: row.revision,
    items: values.map((value) => ({
      sender: { kind: value.sender_kind, id: String(value.sender_id) },
      message: String(value.message),
    })),
  });
}
export function acknowledge(
  sql: TransactionSql,
  ticket_id: string,
  expected_revision: number,
): void {
  sql.run('DELETE FROM ticket_revision_feedback WHERE ticket_id=? AND revision=?', [
    ticket_id,
    expected_revision,
  ]);
}
