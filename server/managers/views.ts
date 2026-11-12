/** Coherent Manager documents and child Ticket reads. */
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Manager } from './contracts.ts';
import { read_manager } from './data.ts';
import { python_json } from './pages.ts';
import {
  has_manager_document_dependencies,
  manager_document_dependencies,
} from './document_dependencies.ts';
import { read_ticket } from '../tickets/data/index.ts';
import { blocked_ticket_ids } from '../core/ticket_blocks.ts';
import { recap_preview } from '../tickets/logic/recap.ts';
import { gating_field } from '../tickets/logic/machine.ts';
import { checkPage, pageFacts } from '../list_reads/logic/index.ts';
import type { ListPageRequest } from '../list_reads/contracts.ts';
export function manager_json(item: Manager): Record<string, unknown> {
  return {
    id: item.id,
    title: item.title,
    settled: item.settled,
    goal: item.goal,
    recap: item.recap,
    page_revision: item.page_revision,
    page_character_count: item.page === null ? 0 : Array.from(python_json(item.page)).length,
    project_id: item.project_id,
    project: item.project_name,
    manager: {
      agent_key: item.manager_agent_key,
      launch_configuration: { ...item.manager_launch_configuration },
    },
    created_at: item.created_at,
    updated_at: item.updated_at,
  };
}
export function manager_detail(sql: ReadSql, id: string): Record<string, unknown> {
  const item = read_manager(sql, id).item;
  const result = manager_json(item);
  (result.manager as Record<string, unknown>).conversation_id =
    sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=?',
      [item.manager_agent_key],
    )?.conversation_id ?? null;
  return result;
}
export function list_managers(sql: ReadSql, project_id: string | null): Record<string, unknown>[] {
  return sql
    .all<{ id: string }>(
      'SELECT id FROM managers' +
        (project_id === null ? '' : ' WHERE project_id=?') +
        ' ORDER BY created_at,id',
      project_id === null ? [] : [project_id],
    )
    .map((row) => manager_detail(sql, row.id));
}
export function list_manager_summaries(
  sql: ReadSql,
  page: ListPageRequest,
  filters: { project_id: string | null; search: string | null; settled: boolean | null },
) {
  checkPage(page);
  const clauses: string[] = [];
  const values: (string | number)[] = [];
  if (filters.project_id !== null) {
    clauses.push('project_id=?');
    values.push(filters.project_id);
  }
  if (filters.settled !== null) {
    clauses.push('settled=?');
    values.push(Number(filters.settled));
  }
  if (filters.search) {
    clauses.push('instr(lower(title), lower(?)) > 0');
    values.push(filters.search);
  }
  const where = clauses.length ? ' WHERE ' + clauses.join(' AND ') : '';
  const count = sql.get<{ count: number }>(
    'SELECT count(*) AS count FROM managers' + where,
    values,
  )!.count;
  const rows = sql
    .all<{ id: string }>(
      'SELECT id FROM managers' + where + ' ORDER BY created_at,id LIMIT ? OFFSET ?',
      [...values, page.limit, page.offset],
    )
    .map((row) => {
      const item = read_manager(sql, row.id).item;
      return {
        id: item.id,
        title: item.title,
        settled: item.settled,
        project_id: item.project_id,
        project: item.project_name,
        created_at: item.created_at,
        updated_at: item.updated_at,
      };
    });
  return { managers: rows, page: pageFacts(count, rows.length, page) };
}
export function manager_tickets(sql: ReadSql, id: string): Record<string, unknown>[] {
  const blocked = blocked_ticket_ids(sql);
  return sql
    .all<{ id: string }>('SELECT id FROM tickets WHERE manager_id=? ORDER BY created_at,id', [id])
    .map((row) => {
      const ticket = read_ticket(sql, row.id);
      const field = gating_field(ticket.workflow, ticket.stage);
      return {
        id: ticket.id,
        title: ticket.title,
        recap: ticket.recap,
        recap_preview: recap_preview(ticket.recap),
        project_id: ticket.project_id,
        manager_id: ticket.manager_id,
        stage: ticket.stage,
        is_backlogged: ticket.is_backlogged,
        activity_at: ticket.updated_at,
        conversation_id: ticket.conversation_id,
        has_pending_proposal: ticket.pending_proposal !== null,
        ceiling_holder: ticket.ceiling_holder,
        ticket_status: ticket.ticket_status,
        waiting_to_closeout: field === 'consequences' && ticket.ticket_status === 'empty',
        gating_field: field,
        blocked: blocked.has(ticket.id),
        employee_backend: ticket.employee_backend,
        worker_type: ticket.worker_type,
      };
    });
}
export function manager_workspace_document(sql: ReadSql, id: string, now: Date) {
  const row = sql.get<{ goal: string; recap: string }>(
    'SELECT goal,recap FROM managers WHERE id=?',
    [id],
  );
  if (!row) throw new PlannerError('not_found', 'manager not found', { id });
  const hasGoals = sql.get(
    "SELECT 1 FROM goals WHERE holder_kind='manager' AND holder_id=? LIMIT 1",
    [id],
  );
  const goals =
    hasGoals || has_manager_document_dependencies()
      ? manager_document_dependencies().manager_goals(sql, id, now)
      : [];
  return { ...row, goals };
}
