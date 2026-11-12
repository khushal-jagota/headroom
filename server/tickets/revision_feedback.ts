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

export function set_feedback(sql: TransactionSql, ticket_id: string, options: {
  stage: string; sender: Principal; message: string; now: number;
}): PendingRevisionFeedback {
  const feedback = JSON.stringify([{sender_kind: options.sender.kind, sender_id: options.sender.id, message: options.message}]).replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  sql.run(`INSERT INTO ticket_revision_feedback
    (ticket_id, stage, feedback_json, revision, created_at, updated_at)
    VALUES (?, ?, ?, 1, ?, ?)
    ON CONFLICT(ticket_id) DO UPDATE SET
      stage=excluded.stage, feedback_json=excluded.feedback_json,
      revision=ticket_revision_feedback.revision+1, updated_at=excluded.updated_at`,
    [ticket_id, options.stage, feedback, options.now, options.now]);
  return new PendingRevisionFeedback({ticket_id, stage: options.stage,
    items: [{sender: options.sender, message: options.message}],
    revision: sql.get<{revision: number}>('SELECT revision FROM ticket_revision_feedback WHERE ticket_id=?', [ticket_id])!.revision});
}
export function discard(sql: TransactionSql, ticket_id: string): void {
  sql.run('DELETE FROM ticket_revision_feedback WHERE ticket_id=?', [ticket_id]);
}
