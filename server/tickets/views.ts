import { python_string_repr } from '../core/python_repr.ts';
import { casefold } from '../core/casefold.ts';
/** Public Ticket reads. All queries share the caller's read snapshot. */
import { dirname, join, parse } from 'node:path';
import { existsSync, lstatSync, readdirSync, statSync } from 'node:fs';
import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { Ticket } from './contracts.ts';
import * as data from './data/index.ts';
import { blocker_summary } from '../core/ticket_blocks.ts';
import { workflow_manifest } from '../worker_types/registry.ts';
import { checkPage, pageFacts } from '../list_reads/logic/index.ts';
import type { ListPageRequest } from '../list_reads/contracts.ts';
export type Json = Record<string, unknown>;

export function broken_ticket(sql: ReadSql, id: string): Json | null {
  const row = sql.get<Record<string, unknown>>(
    'SELECT t.*, CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END AS effective_project_id, p.name AS project_name, m.title AS manager_title FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id LEFT JOIN projects p ON p.id=CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END WHERE t.id=?',
    [id],
  );
  if (!row?.integrity_error_reason) return null;
  const structural = new Set([
    'PlannerError: unknown worker type (worker_type)',
    'PlannerError: stage outside the linear order (stage)',
    "PlannerError: ceiling outside the type's range (ceiling)",
    'PlannerError: ceiling holder does not exist (ceiling_holder)',
    'PlannerError: a Ticket cannot hold its own ceiling (ceiling_holder)',
    'PlannerError: corrupt ticket field state (fields or proposal)',
    'PlannerError: unknown agent backend (employee_backend)',
    'PlannerError: invalid stored Ticket structure',
    'JSONDecodeError: malformed stored JSON',
    'KeyError: missing stored Ticket member',
    'TypeError: invalid stored Ticket member type',
    'ValueError: invalid stored Ticket member value',
  ]);
  const reason = structural.has(String(row.integrity_error_reason))
    ? row.integrity_error_reason
    : 'TicketIntegrityError: structural fault reason unavailable';
  return {
    id: row.id,
    title: row.title,
    worker_type: row.worker_type,
    stage: row.stage,
    ticket_status: 'errored',
    integrity_error: { reason },
    recap: row.recap,
    recap_updated_at: row.recap_updated_at,
    recap_stage_changed: Boolean(row.recap_stage_changed),
    conversation_id: row.conversation_id,
    manager_id: row.manager_id,
    manager_kind: row.manager_kind,
    manager_title: row.manager_title,
    project_id: row.effective_project_id,
    project: row.project_name,
    is_backlogged: Boolean(row.is_backlogged),
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}
export function blocker_summary_json(summary: ReturnType<typeof blocker_summary>): Json {
  return {
    blocked_by: summary.blocked_by
      .filter((r) => r.active)
      .map((r) => ({ ticket_id: r.ticket_id, title: r.title, stage: r.stage, href: r.href })),
  };
}
export function ticket_json(ticket: Ticket, _now = 0): Json {
  return {
    is_backlogged: ticket.is_backlogged,
    id: ticket.id,
    title: ticket.title,
    worker_type: ticket.worker_type,
    workflow: workflow_manifest(
      ticket.workflow,
      ticket.worker_type,
      ticket.workflow.specialist_skill,
    ),
    locale: ticket.locale,
    employee_backend: ticket.employee_backend,
    employee_launch_model: ticket.employee_launch_model,
    employee_launch_reasoning_effort: ticket.employee_launch_reasoning_effort,
    stage: ticket.stage,
    deadline: ticket.deadline,
    project_id: ticket.project_id,
    project: ticket.project_name,
    manager_id: ticket.manager_id,
    manager: {
      kind: ticket.manager_kind,
      id: ticket.manager_kind === 'manager' ? ticket.manager_id : 'chief',
    },
    manager_title: ticket.manager_title,
    recap: ticket.recap,
    recap_updated_at: ticket.recap_updated_at,
    recap_stage_changed: ticket.recap_stage_changed,
    ceiling: ticket.ceiling,
    ceiling_holder: ticket.ceiling_holder,
    ticket_status: ticket.ticket_status,
    conversation_id: ticket.conversation_id,
    field_values: { ...ticket.field_values },
    field_approvals: ticket.field_approvals,
    pending_proposal:
      ticket.pending_proposal === null
        ? null
        : { ...ticket.pending_proposal, revision: ticket.pending_proposal_revision },
    created_at: ticket.created_at,
    updated_at: ticket.updated_at,
  };
}
export function ticket_creation_json(sql: ReadSql, ticket: Ticket, now: number): Json {
  const holder = ticket.ceiling_holder;
  const recipient =
    holder.kind === 'chief'
      ? 'Chief'
      : holder.kind === 'manager'
        ? `the manager ${sql.get<{ title: string }>('SELECT title FROM managers WHERE id=?', [holder.id])!.title}`
        : 'the owner';
  const stages = ticket.workflow.stages.filter((s) => !s.is_terminal),
    current = ticket.workflow.stages.findIndex((s) => s.id === ticket.stage),
    cap = ticket.workflow.stages.findIndex((s) => s.id === ticket.ceiling);
  const remaining = stages.filter((s) => ticket.workflow.stages.indexOf(s) >= current);
  const automatic = remaining.filter((s) => ticket.workflow.stages.indexOf(s) < cap),
    autonomous = automatic.filter((s) => s.ownership_mode === 'worker'),
    user = automatic.filter((s) => s.ownership_mode === 'user');
  const review = remaining.find((s) => !automatic.includes(s));
  const parts: string[] = [];
  if (!review) parts.push('Nothing in this grant ever parks for review.');
  parts.push(
    autonomous.length
      ? 'Runs unsupervised: ' + autonomous.map((s) => s.label).join(', ') + '.'
      : 'Nothing runs unsupervised.',
  );
  if (user.length)
    parts.push(
      'User involvement is required at ' +
        user.map((s) => s.label).join(', ') +
        ', even though its proposals auto-accept.',
    );
  if (review) {
    parts.push(`First parks for review at ${review.label}, with ${recipient}.`);
    if (review.ownership_mode === 'user')
      parts.push(`${review.label} requires user involvement before its proposal review.`);
  }
  return { ...ticket_json(ticket, now), supervision_receipt: parts.join(' ') };
}
interface Artifact {
  name: string;
  opens: string | null;
  modified_at: number;
  children: Artifact[];
}
function newest(path: string): number {
  return Math.max(
    0,
    ...readdirSync(path)
      .filter((n) => !lstatSync(join(path, n)).isSymbolicLink())
      .map((n) => {
        const p = join(path, n),
          s = statSync(p);
        return s.isFile() ? s.mtimeMs / 1000 : s.isDirectory() ? newest(p) : 0;
      }),
  );
}
function fold(root: string, prefix = ''): Artifact[] {
  if (!existsSync(root) || lstatSync(root).isSymbolicLink() || !statSync(root).isDirectory())
    return [];
  const result: Artifact[] = [];
  for (const name of readdirSync(root).sort()) {
    const path = join(root, name),
      s = lstatSync(path),
      opens = prefix + name;
    if (s.isSymbolicLink()) continue;
    if (s.isFile()) result.push({ name, opens, modified_at: s.mtimeMs / 1000, children: [] });
    else if (s.isDirectory()) {
      const index = join(path, 'index.html');
      if (existsSync(index) && !lstatSync(index).isSymbolicLink() && statSync(index).isFile())
        result.push({
          name,
          opens: opens + '/index.html',
          modified_at: newest(path),
          children: [],
        });
      else {
        const children = fold(path, opens + '/');
        if (children.length)
          result.push({
            name,
            opens: null,
            modified_at: Math.max(...children.map((c) => c.modified_at)),
            children,
          });
      }
    }
  }
  return result.sort(
    (a, b) =>
      b.modified_at - a.modified_at ||
      (a.name.toLowerCase() < b.name.toLowerCase()
        ? -1
        : a.name.toLowerCase() > b.name.toLowerCase()
          ? 1
          : a.name < b.name
            ? -1
            : a.name > b.name
              ? 1
              : 0),
  );
}
function ticket_artifacts(filename: string | undefined, id: string): Artifact[] {
  if (!filename) return [];
  const root = join(dirname(filename), 'files', 'tickets', id);
  for (let p = root; ; p = dirname(p)) {
    if (existsSync(p) && lstatSync(p).isSymbolicLink()) return [];
    if (p === parse(p).root) break;
  }
  return fold(root);
}
export function ticket_detail(sql: ReadSql, id: string, now: number, db_path?: string): Json {
  const broken = broken_ticket(sql, id);
  if (broken) return broken;
  const ticket = data.read_ticket(sql, id),
    summary = blocker_summary(sql, id);
  const result = {
    ...ticket_json(ticket, now),
    record_sha256: data.ticket_record_sha256(sql, id),
    artifacts: ticket_artifacts(db_path, id),
    blocked: summary.blocked,
    employee_configuration_editable: data.employee_configuration_editable(ticket),
    conversation_history: sql.all<{ conversation_id: string; created_at: number }>(
      'SELECT tc.conversation_id,c.created_at FROM ticket_conversations tc JOIN conversations c ON c.conversation_id=tc.conversation_id WHERE tc.ticket_id=? ORDER BY c.created_at,tc.conversation_id',
      [id],
    ),
  };
  return summary.blocked ? { ...result, blocker_summary: blocker_summary_json(summary) } : result;
}
export function list_tickets(
  sql: ReadSql,
  now: number,
  stage: string | null,
  project_id: string | null,
  manager_id: string | null,
): Json[] {
  const clauses: string[] = [],
    params: string[] = [];
  if (stage !== null) {
    clauses.push('t.stage=?');
    params.push(stage);
  }
  if (project_id !== null) {
    clauses.push('CASE WHEN t.manager_id IS NOT NULL THEN m.project_id ELSE t.project_id END=?');
    params.push(project_id);
  }
  if (manager_id !== null) {
    clauses.push('t.manager_id=?');
    params.push(manager_id);
  }
  return sql
    .all<{ id: string }>(
      'SELECT t.id FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id' +
        (clauses.length ? ' WHERE ' + clauses.join(' AND ') : '') +
        ' ORDER BY t.created_at,t.id',
      params,
    )
    .map((r) => broken_ticket(sql, r.id) ?? ticket_json(data.read_ticket(sql, r.id), now));
}
export interface TicketListFilters {
  manager: Principal | null;
  stages: string[];
  excluded_stages: string[];
  ticket_statuses: string[];
  excluded_ticket_statuses: string[];
  is_backlogged: boolean | null;
  worker_types: string[];
  include_terminal: boolean;
  search: string | null;
}
export function list_ticket_summaries(
  sql: ReadSql,
  page: ListPageRequest,
  filters: TicketListFilters,
  project_id: string | null,
  manager_id: string | null,
): { tickets: Json[]; page: ReturnType<typeof pageFacts> } {
  checkPage(page);
  const records = sql.all<{
    id: string;
    value: string;
    project_name: string | null;
    manager_title: string | null;
    recap: string;
    field_values: string;
    pending_proposal: string | null;
  }>(
    "SELECT r.id,r.value,p.name AS project_name,m.title AS manager_title,t.recap,t.field_values,t.pending_proposal FROM navigation_records r LEFT JOIN managers m ON m.id=json_extract(r.value,'$.manager_id') LEFT JOIN projects p ON p.id=json_extract(r.value,'$.project_id') JOIN tickets t ON t.id=r.id WHERE r.kind='ticket' ORDER BY json_extract(r.value,'$.created_at'),r.id",
  );
  const matches: Json[] = [];
  for (const row of records) {
    const v = JSON.parse(row.value) as Json,
      status = String(v.ticket_status);
    if (
      filters.manager &&
      (v.manager_kind !== filters.manager.kind ||
        (filters.manager.kind === 'manager' && v.manager_id !== filters.manager.id))
    )
      continue;
    if (
      (project_id !== null && v.project_id !== project_id) ||
      (manager_id !== null && v.manager_id !== manager_id)
    )
      continue;
    if (
      (filters.ticket_statuses.length && !filters.ticket_statuses.includes(status)) ||
      filters.excluded_ticket_statuses.includes(status)
    )
      continue;
    if ('integrity_error' in v) {
      const broken = broken_ticket(sql, row.id)!;
      if (
        filters.search !== null &&
        !casefold(python_repr(broken)).includes(casefold(filters.search))
      )
        continue;
      matches.push(broken);
      continue;
    }
    if (filters.is_backlogged !== null && Boolean(v.is_backlogged) !== filters.is_backlogged)
      continue;
    if (filters.worker_types.length && !filters.worker_types.includes(String(v.worker_type)))
      continue;
    if (!filters.include_terminal && v.terminal) continue;
    if (
      (filters.stages.length && !filters.stages.includes(String(v.stage))) ||
      filters.excluded_stages.includes(String(v.stage))
    )
      continue;
    if (filters.search) {
      const ticket = data.read_ticket(sql, row.id);
      const text = [
        v.title,
        row.recap,
        ...ticket.workflow.fields
          .filter((field) => Object.hasOwn(ticket.field_values, field.id))
          .map((field) => ticket.field_values[field.id]),
        row.pending_proposal ? (JSON.parse(row.pending_proposal) as { body: string }).body : '',
      ].join('\n');
      if (!casefold(text).includes(casefold(filters.search))) continue;
    }
    matches.push({
      is_backlogged: Boolean(v.is_backlogged),
      waiting_to_closeout: v.gating_field === 'consequences' && status === 'empty',
      id: row.id,
      title: v.title,
      locale: v.locale,
      worker_type: v.worker_type,
      stage: v.stage,
      ticket_status: status,
      project_id: v.project_id,
      project: row.project_name,
      manager_id: v.manager_id,
      manager: { kind: v.manager_kind, id: v.manager_id ?? 'chief' },
      manager_title: row.manager_title,
      recap: v.recap,
      recap_preview: v.recap_preview,
      recap_updated_at: v.recap_updated_at,
      recap_stage_changed: Boolean(v.recap_stage_changed),
    });
  }
  const tickets = matches.slice(page.offset, page.offset + page.limit);
  return { tickets, page: pageFacts(matches.length, tickets.length, page) };
}
export function copy_text(sql: ReadSql, id: string): string {
  const broken = broken_ticket(sql, id);
  if (broken) return python_repr(broken);
  const ticket = data.read_ticket(sql, id),
    ownership = ticket.workflow.stages.find((s) => s.id === ticket.stage)?.ownership_mode ?? null,
    summary = blocker_summary(sql, id);
  const show = (value: string | null | undefined) => value || '(none)';
  const field_blocks = ticket.workflow.fields
    .map((f) => `${f.id}:\n${show(ticket.field_values[f.id])}\n\n`)
    .join('');
  const blockers =
    summary.blocked_by
      .filter((r) => r.active)
      .map((r) => `- ${r.title} (${r.ticket_id}, ${r.stage})`)
      .join('\n') || '(none)';
  return `${ticket.title}\nstage: ${ticket.stage}\nemployee_backend: ${ticket.employee_backend}\nowner: ${ownership ?? '(none)'}\n\n${field_blocks}pending proposal:\n${show(ticket.pending_proposal?.body)}\nrecap:\n${show(ticket.recap)}\n\nblocked_by:\n${blockers}\n`;
}
function python_repr(value: unknown): string {
  if (value === null) return 'None';
  if (value === true) return 'True';
  if (value === false) return 'False';
  if (typeof value === 'string') return python_string_repr(value);
  if (Array.isArray(value)) return '[' + value.map(python_repr).join(', ') + ']';
  if (typeof value === 'object')
    return (
      '{' +
      Object.entries(value as Json)
        .map(([k, v]) => python_repr(k) + ': ' + python_repr(v))
        .join(', ') +
      '}'
    );
  return String(value);
}
export function review_view(sql: ReadSql): Json {
  const items: Json[] = [];
  for (const { id } of sql.all<{ id: string }>('SELECT id FROM tickets ORDER BY id')) {
    if (broken_ticket(sql, id)) continue;
    const t = data.read_ticket(sql, id),
      field = t.workflow.stages.find((s) => s.id === t.stage)?.gating_field;
    if (
      t.ticket_status === 'awaiting_approval' &&
      !t.is_backlogged &&
      t.ceiling_holder.kind === 'owner' &&
      field &&
      t.pending_proposal
    )
      items.push({
        review_item_type: 'proposal',
        ticket_id: id,
        field,
        title: t.title,
        waiting_since: t.pending_proposal.created_at,
      });
  }
  const messages: Json[] = [];
  for (const conversation of sql.all<{ id: string; value: string }>(
    "SELECT id,value FROM navigation_records WHERE kind='conversation' ORDER BY id",
  )) {
    const c = JSON.parse(conversation.value) as {
      attention: { durable: { message_for_owner: boolean } };
      first_unread_owner_message_at: number;
    };
    if (!c.attention.durable.message_for_owner) continue;
    const row = sql.get<{ id: string; value: string }>(
      "SELECT a.id,a.value FROM navigation_records a JOIN navigation_records owner ON owner.kind=json_extract(a.value,'$.owner_kind') AND owner.id=json_extract(a.value,'$.owner_id') WHERE a.kind='address' AND json_extract(a.value,'$.conversation_id')=? ORDER BY json_extract(a.value,'$.current') DESC,a.id LIMIT 1",
      [conversation.id],
    );
    if (!row) continue;
    const address = JSON.parse(row.value) as {
      owner_kind: string;
      owner_id: string;
      current: boolean;
    };
    const owner = JSON.parse(
      sql.get<{ value: string }>('SELECT value FROM navigation_records WHERE kind=? AND id=?', [
        address.owner_kind,
        address.owner_id,
      ])!.value,
    ) as Json;
    if (
      (address.owner_kind === 'ticket' && (owner.stage === 'done' || owner.is_backlogged)) ||
      (address.owner_kind === 'manager' && owner.settled)
    )
      continue;
    messages.push({
      review_item_type: 'message',
      conversation_id: conversation.id,
      address_id: row.id,
      owner_kind: address.owner_kind,
      owner_id: address.owner_id,
      title: owner.title ?? owner.label ?? '',
      waiting_since: c.first_unread_owner_message_at,
      recap: owner.recap ?? '',
      recap_reference: null,
      _current: address.current,
    });
  }
  messages.sort(
    (a, b) =>
      Number(b._current) - Number(a._current) ||
      String(a.address_id).localeCompare(String(b.address_id)) ||
      String(a.conversation_id).localeCompare(String(b.conversation_id)),
  );
  for (const m of messages) {
    delete m._current;
    items.push(m);
  }
  const tickets = new Map<string, Json>(),
    result: Json[] = [];
  for (const item of items) {
    if (item.review_item_type === 'message' && item.owner_kind !== 'ticket') {
      result.push(item);
      continue;
    }
    const id = String(item.review_item_type === 'proposal' ? item.ticket_id : item.owner_id);
    let card = tickets.get(id);
    if (!card) {
      card = {
        review_item_type: 'ticket',
        ticket_id: id,
        title: item.title,
        waiting_since: item.waiting_since,
        proposal: null,
        messages: [],
      };
      tickets.set(id, card);
    }
    card.waiting_since = Math.min(Number(card.waiting_since), Number(item.waiting_since));
    if (item.review_item_type === 'proposal') card.proposal = item;
    else (card.messages as Json[]).push(item);
  }
  result.push(...tickets.values());
  result.sort(
    (a, b) =>
      Number(a.waiting_since) - Number(b.waiting_since) ||
      String(a.review_item_type).localeCompare(String(b.review_item_type)) ||
      String(a.conversation_id ?? a.ticket_id).localeCompare(
        String(b.conversation_id ?? b.ticket_id),
      ),
  );
  return { items: result };
}
