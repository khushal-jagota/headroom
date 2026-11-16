import type {
  WorkspaceFieldBody,
  WorkspaceTicket,
} from '../../../web/src/lib/ticketWorkspace/contracts.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { workflow_from_row } from '../../worker_types/store.ts';
import * as data from '../data/index.ts';
import { blocker_summary } from '../../core/ticket_blocks.ts';
import { broken_ticket, blocker_summary_json, ticket_json } from '../views.ts';

export function workspace_ticket(sql: ReadSql, ticket_id: string): WorkspaceTicket {
  const broken = broken_ticket(sql, ticket_id);
  if (broken) return broken as unknown as WorkspaceTicket;
  const ticket = data.read_ticket(sql, ticket_id),
    definition = ticket.workflow,
    gate = definition.stages.find((s) => s.id === ticket.stage)?.gating_field;
  const detail = ticket_json(ticket),
    workflow = detail.workflow as { guidance?: string; stages: Record<string, unknown>[] };
  delete workflow.guidance;
  workflow.stages = workflow.stages.map(({ instructions: _instructions, ...s }) => s);
  const summary = blocker_summary(sql, ticket_id);
  return {
    ...detail,
    workflow,
    field_values: gate ? { [gate]: ticket.field_values[gate] ?? '' } : {},
    field_summaries: Object.fromEntries(
      sql
        .all<{ field: string; revision: number; byte_count: number }>(
          'SELECT field,revision,byte_count FROM ticket_workspace_fields WHERE ticket_id=? ORDER BY field',
          [ticket_id],
        )
        .map((r) => [r.field, { revision: String(r.revision), byte_count: r.byte_count }]),
    ),
    record_sha256: data.ticket_record_sha256(sql, ticket_id),
    blocked: summary.blocked,
    blocker_summary: blocker_summary_json(summary),
    employee_configuration_editable: data.employee_configuration_editable(ticket),
  } as unknown as WorkspaceTicket;
}
export function field_revision(sql: ReadSql, ticket_id: string, field: string): string {
  const row = sql.get<{ workflow_json: string }>('SELECT workflow_json FROM tickets WHERE id=?', [
    ticket_id,
  ]);
  if (!row) throw new PlannerError('not_found', 'ticket not found', { ticket_id });
  const definition = workflow_from_row(row);
  if (
    !definition.fields.some((f) => f.id === field) &&
    !sql.get('SELECT 1 FROM tickets,json_each(tickets.field_values) WHERE tickets.id=? AND key=?', [
      ticket_id,
      field,
    ])
  )
    throw new PlannerError('not_found', 'Ticket field not found', { field });
  return String(
    sql.get<{ revision: number }>(
      'SELECT revision FROM ticket_workspace_fields WHERE ticket_id=? AND field=?',
      [ticket_id, field],
    )?.revision ?? 0,
  );
}
export function workspace_field(
  sql: ReadSql,
  ticket_id: string,
  field: string,
): WorkspaceFieldBody {
  const revision = field_revision(sql, ticket_id, field);
  const row = sql.get<{ value: string }>(
    'SELECT value FROM tickets,json_each(tickets.field_values) WHERE tickets.id=? AND key=?',
    [ticket_id, field],
  );
  return { ticket_id, field, revision, value: row?.value ?? '' };
}
