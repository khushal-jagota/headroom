/** The HTTP-client commands used for Panels work. Domain decisions stay on the server. */
import {
  worker_setup_step_label,
  type WorkerSetupStep,
} from '../ticket_workspaces/worker_setup.ts';
import type { CommandResult, Invocation } from './contracts.ts';
import { send, send_text, emit, fail_validation, json } from './http.ts';
import { read_json_input, read_named_file, read_text } from './input.ts';
import { part, parse_part_names, project_record, render_text } from './record_projection.ts';
import type { RecordParts } from './record_projection.ts';
import { numeric_value } from '../core/http/numeric_json.ts';
import { scalar_text as scalar } from './text.ts';
import { ticket_argument } from './parser.ts';
import { recap_preview } from '../tickets/logic/recap.ts';
import { join_command, require_released_revision } from '../installation/join_command.ts';
import { need_counts_display, need_display } from './needs.ts';
import { ticketStatusLabel as ticket_status_label } from '../../web/src/lib/ticketStatusPresentation.ts';
import { response_field } from './response_field.ts';
import { CliFailure } from './errors.ts';
import { require_current_server } from './current_server.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import type {
  WorkMoveRequest,
  WorkMoveResult,
  WorkMoveBatchResult,
} from '../work_moves/contracts.ts';

type Body = Record<string, any>;
const quote = (value: string, slash = false): string => {
  const encoded = encodeURIComponent(value).replace(
    /[!'()*]/g,
    (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
  );
  return slash ? encoded.replace(/%2F/g, '/') : encoded;
};
/** A field the response states, even as null. */
const has = (record: unknown, key: string): boolean =>
  record !== null && typeof record === 'object' && Object.hasOwn(record, key);
const drop_none = (body: Body): Body =>
  Object.fromEntries(
    Object.entries(body).filter(([, value]) => value !== null && value !== undefined),
  );
const lines = (rows: any[], format: (row: any) => string): string =>
  rows.length ? rows.map(format).join('\n') : '(none)';
const raw = (stdout: string): CommandResult => ({ exit_code: 0, stdout, stderr: '' });
const now_seconds = () => Math.trunc(Date.now() / 1000);
/** `45s`, `20m`, `5h`, `3d`. */
function age_text(since: unknown): string {
  const age = Math.max(0, now_seconds() - Math.trunc(numeric_value(since)!));
  return age < 60
    ? `${age}s`
    : age < 3600
      ? `${Math.floor(age / 60)}m`
      : age < 86400
        ? `${Math.floor(age / 3600)}h`
        : `${Math.floor(age / 86400)}d`;
}
function format_recap(item: Body): string {
  const recap = scalar(item.recap);
  if (!recap.trim()) return 'no recap';
  let freshness =
    item.recap_updated_at != null
      ? `recap ${age_text(item.recap_updated_at)} old`
      : 'recap age unknown';
  if (item.recap_stage_changed) freshness += ', stage changed since';
  return `${recap_preview(recap)} (${freshness})`;
}
const plural = (count: unknown, one: string, many: string) =>
  `${scalar(count)} ${numeric_value(count) === 1 ? one : many}`;
/** Left-align each column of `rows` to its widest cell; the last column is not padded. */
function columns(rows: string[][]): string[] {
  const widths = rows[0]?.map((_, index) => Math.max(...rows.map((row) => row[index].length)));
  return rows.map((row) =>
    row
      .map((cell, index) => (index === row.length - 1 ? cell : cell.padEnd(widths[index])))
      .join('  '),
  );
}
const ticket_id = (inv: Invocation, positional: string | null | undefined): string => {
  const value = ticket_argument(positional, inv.environment);
  if (value !== undefined) return value;
  return fail_validation(inv, 'ticket id required: pass it or set PLAN_TICKET_ID');
};
/** The caller's own record, from the identity Panels put in its environment. */
type Caller = { kind: 'ticket' | 'manager'; id: string } | { kind: 'chief' } | null;
function caller(inv: Invocation): Caller {
  const actor = inv.environment.PLAN_ACTOR?.trim();
  const tid = inv.environment.PLAN_TICKET_ID?.trim();
  const mid = inv.environment.PLAN_MANAGER_ID?.trim();
  if (actor === 'chief') return { kind: 'chief' };
  if (actor === 'worker' && tid) return { kind: 'ticket', id: tid };
  if (actor === 'manager' && mid) return { kind: 'manager', id: mid };
  return null;
}
function own_ticket(inv: Invocation): string {
  const self = caller(inv);
  if (self?.kind === 'ticket') return self.id;
  return fail_validation(inv, 'this process has no Ticket identity (PLAN_TICKET_ID)', {
    no_route: 'Only the Worker of a Ticket can do this, from its own conversation.',
  });
}
/**
 * The next holder: `owner`, `chief`, or a Manager id. With no word it is the caller. `me` is
 * refused: on older command lines it named the owner, so an agent with old text in its
 * context must not have it change meaning without notice.
 */
function holder(inv: Invocation, value: string | null): Body {
  const word = value?.trim().toLowerCase();
  if (word === 'owner') return { kind: 'owner', id: 'owner' };
  if (word === 'chief') return { kind: 'chief', id: 'chief' };
  if (word === 'me')
    return fail_validation(inv, '"me" is no longer a holder word', {
      no_route:
        'Use --holder owner, --holder chief, or --holder MANAGER_ID. With no --holder, the holder is the caller.',
    });
  if (value != null) return { kind: 'manager', id: value.trim() };
  const self = caller(inv);
  if (self === null) return { kind: 'owner', id: 'owner' };
  if (self.kind === 'chief') return { kind: 'chief', id: 'chief' };
  if (self.kind === 'manager') return { kind: 'manager', id: self.id };
  return fail_validation(
    inv,
    'a Worker cannot hold a ceiling; pass --holder owner, chief, or a Manager id',
  );
}
const principal_text = (value: Body | null | undefined): string =>
  value == null
    ? 'none'
    : value.kind === 'manager'
      ? `manager ${scalar(value.id)}`
      : scalar(value.kind);
/** The paging facts, for a page that does not hold every match. */
function paging_lines(page: Body): string[] {
  const facts = `Returned ${scalar(page.return_count)} of ${scalar(page.match_count)} matches. Omitted ${scalar(page.omitted_before)} before and ${scalar(page.omitted_after)} after. Complete: ${scalar(page.complete ? 'yes' : 'no')}.`;
  const directions: string[] = [];
  if (numeric_value(page.omitted_before))
    directions.push('Use --offset 0 to start from the first match.');
  if (page.next_offset != null)
    directions.push(
      `Use --limit ${scalar(page.limit)} --offset ${scalar(page.next_offset)} for the next page.`,
    );
  return [facts, ...directions];
}
function paged(rows: string, page: Body): string {
  const result =
    numeric_value(page.match_count) === 0
      ? 'No matches.'
      : numeric_value(page.return_count) === 0
        ? 'No results at this offset.'
        : rows;
  return [result, ...paging_lines(page)].join('\n');
}
function record_parts(inv: Invocation, names: readonly string[]): string[] | null {
  try {
    return parse_part_names(names);
  } catch (error) {
    return fail_validation(inv, (error as Error).message);
  }
}
function emit_record(
  inv: Invocation,
  header: Body,
  parts: RecordParts,
  names: readonly string[],
  human_header: Body = header,
): CommandResult {
  let projection: Body;
  const requested = record_parts(inv, names);
  try {
    projection = project_record(header, parts, requested);
  } catch (error) {
    const no_route = (requested ?? []).includes('rejection')
      ? 'Revision feedback arrives in the Worker prompt. No panels command reads it.'
      : undefined;
    return fail_validation(inv, (error as Error).message, { no_route });
  }
  return emit(
    projection,
    inv.values.as_json,
    render_text({ ...projection, header: human_header }, requested ?? [...parts.keys()]),
  );
}
/** "preparing workspace for 2m 19s" */
function setting_up_text(setup: { step: WorkerSetupStep; began_at: unknown }): string {
  // The CLI reads JSON numbers as JsonNumber values.
  const began_at = numeric_value(setup.began_at) ?? now_seconds();
  const seconds = Math.max(0, now_seconds() - began_at);
  const elapsed =
    seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60)}s`;
  return `${worker_setup_step_label(setup.step)} for ${elapsed}`;
}
const TICKET_HEADER_KEYS = [
  'id',
  'title',
  'worker_type',
  'locale',
  'machine_id',
  'machine_name',
  'employee_backend',
  'employee_launch_model',
  'employee_launch_reasoning_effort',
  'stage',
  'ticket_status',
  'agent_state',
  'is_backlogged',
  'record_sha256',
  'deadline',
  'project_id',
  'manager_id',
  'ceiling',
  'ceiling_holder',
  'needs',
];
/** The human header leads with the work summary; JSON keeps its raw fields. */
function ticket_headers(data: Body): [Body, Body] {
  const header = Object.fromEntries(
    TICKET_HEADER_KEYS.filter((key) => key in data).map((key) => [key, data[key]]),
  );
  if (data.worker_setup) header.setting_up = setting_up_text(data.worker_setup);
  const human: Body = {};
  if ('title' in data) human.title = data.title;
  human.recap = format_recap(data);
  if ('ticket_status' in data) human.status = ticket_status_label(data.ticket_status);
  if ('agent_state' in data) human.worker = data.agent_state;
  if ('stage' in data) human.stage = data.stage;
  if ('needs' in data) human.needs = need_display(data.needs, data.ceiling_holder?.kind);
  if ('ceiling' in data) human.ceiling = data.ceiling;
  if ('ceiling_holder' in data) human.ceiling_holder = principal_text(data.ceiling_holder);
  const blockers: Body[] = (data.blocker_summary?.blocked_by ?? []).filter(
    (blocker: Body) => blocker.active,
  );
  if (blockers.length)
    human.blocked_by = blockers
      .map((b) => `${scalar(b.title)} (${scalar(b.ticket_id)}, ${scalar(b.stage)})`)
      .join('; ');
  const hidden = ['needs', 'ceiling_holder', 'ticket_status', 'agent_state', 'is_backlogged'];
  for (const [key, value] of Object.entries(header))
    if (!(key in human) && !hidden.includes(key)) human[key] = value;
  return [header, human];
}
async function ticket_parts(
  inv: Invocation,
  data: Body,
  names: readonly string[],
): Promise<RecordParts> {
  const parts: RecordParts = new Map();
  for (const field of data.workflow.fields)
    parts.set(
      field.id,
      part(Object.hasOwn(data.field_values, field.id) ? data.field_values[field.id] : null, {
        approval: Object.hasOwn(data.field_approvals ?? {}, field.id)
          ? data.field_approvals[field.id]
          : undefined,
      }),
    );
  parts.set('workflow', part(data.workflow ?? null));
  parts.set('proposal', part(data.pending_proposal ?? null));
  parts.set('recap', part(data.recap ?? ''));
  for (const field of new Set(record_parts(inv, names) ?? [])) {
    if (parts.has(field) || field === 'rejection') continue;
    const body = await send(
      inv,
      'GET',
      `/api/tickets/${quote(String(data.id))}/workspace/fields/${quote(field)}`,
    );
    parts.set(field, part(body.value));
  }
  return parts;
}
async function show_ticket(inv: Invocation): Promise<CommandResult> {
  const positional = [inv.values.ticket_id, ...(inv.values.part_names ?? [])].filter(
    (word): word is string => word != null,
  );
  if (positional[0] === '-') positional.shift();
  const named = positional[0]?.startsWith('t_') ? positional.shift()! : null;
  const tid = named ?? ticket_id(inv, null);
  const data = await send(
    inv,
    'GET',
    named ? '/api/tickets' : `/api/tickets/${tid}/worker-self`,
    named ? { params: { detail: 'full', id: tid } } : {},
  );
  require_current_server(inv, has(data, 'needs'));
  const [header, human] = ticket_headers(data);
  return emit_record(inv, header, await ticket_parts(inv, data, positional), positional, human);
}
/** skill edit and worker-type skill: a description, a body, or both. */
async function skill_patch(inv: Invocation): Promise<Body> {
  const patch: Body = {};
  if (inv.values.description != null) {
    if (!inv.values.description.trim()) fail_validation(inv, 'description must not be blank');
    patch.description = inv.values.description;
  }
  const body = await read_text(inv, {
    inline: 'message',
    label: 'skill body',
    optional: 'description' in patch,
  });
  if (body !== null) patch.markdown_body = body;
  return patch;
}
async function approve(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const tid: string = v.ticket_id;
  const detail = await send(inv, 'GET', '/api/tickets', { params: { detail: 'full', id: tid } });
  const stage = detail.stage;
  const field =
    detail.workflow.stages.find((candidate: Body) => candidate.id === stage)?.gating_field ?? null;
  if (field === null)
    fail_validation(inv, `ticket in ${scalar(stage)} has nothing to approve`, {
      no_route: 'No panels approval call is available for this Ticket.',
    });
  if (detail.pending_proposal === null || detail.pending_proposal.field !== field)
    fail_validation(inv, `no pending ${scalar(field)} proposal`, {
      no_route: 'No panels approval call is available until a proposal exists.',
    });
  const revision = v.expected_proposal_revision ?? detail.pending_proposal.revision;
  if (scalar(revision) !== scalar(detail.pending_proposal.revision))
    fail_validation(inv, 'the visible proposal changed; read it again before approving');
  const final_approval = detail.workflow.advance?.[stage] === 'done';
  if (v.ceiling == null && !final_approval) fail_validation(inv, 'approval requires --ceiling');
  if (v.edit_file != null && v.kickoff_note_file != null)
    fail_validation(inv, 'approval accepts only one edited body option');
  if ((v.kickoff_title != null || v.kickoff_note_file != null) && field !== 'brief')
    fail_validation(
      inv,
      '--kickoff-title and --kickoff-note-file only apply while approving the brief',
    );
  const payload: Body = {
    expected_proposal_revision: revision,
    decision_id: `${tid}:${scalar(revision)}:accept`,
    ...(!final_approval
      ? { next_ceiling: v.ceiling, next_holder: holder(inv, v.holder ?? null) }
      : {}),
  };
  const edited = v.edit_file ?? v.kickoff_note_file;
  if (edited != null) payload.edited_body = await read_named_file(inv, edited);
  if (v.kickoff_title != null)
    await send(inv, 'PATCH', `/api/tickets/${tid}`, { json_body: { title: v.kickoff_title } });
  const data = await send(inv, 'POST', `/api/tickets/${tid}/accept/${field}`, {
    json_body: payload,
  });
  return emit(data, v.as_json, `${scalar(data.id)} approved ${scalar(field)}`);
}
async function send_back(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const tid: string = v.ticket_id;
  const guidance = await read_text(inv, { inline: 'message', label: 'guidance' });
  let revision = v.expected_proposal_revision;
  if (revision == null) {
    const detail = await send(inv, 'GET', '/api/tickets', { params: { detail: 'full', id: tid } });
    if (!detail.pending_proposal) fail_validation(inv, 'no pending proposal to send back');
    revision = detail.pending_proposal.revision;
  }
  const data = await send(inv, 'POST', `/api/tickets/${tid}/reject`, {
    json_body: {
      message: guidance,
      expected_proposal_revision: revision,
      decision_id: `${tid}:${scalar(revision)}:reject`,
    },
  });
  return emit(data, v.as_json, `${scalar(tid)} proposal sent back`);
}
const SCOPE_NOUNS: Record<string, [string, string]> = {
  live: ['live Ticket', 'live Tickets'],
  backlog: ['Ticket in backlog', 'Tickets in backlog'],
  done: ['done Ticket', 'done Tickets'],
  all: ['Ticket', 'Tickets'],
};
async function list_tickets(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const scopes = (['backlog', 'done', 'all'] as const).filter((scope) => v[scope]);
  if (scopes.length > 1) fail_validation(inv, 'use only one of --backlog, --done, or --all');
  if (v.manager != null && v.chief) fail_validation(inv, 'use only one of --manager or --chief');
  const scope = scopes[0] ?? 'live';
  const data = await send(inv, 'GET', '/api/tickets', {
    params: drop_none({
      detail: 'summary',
      scope,
      needs: v.needs?.length ? v.needs : null,
      manager_id: v.manager,
      manager_kind: v.chief ? 'chief' : null,
      worker_type: v.worker_type?.length ? v.worker_type : null,
      stage: v.stage?.length ? v.stage : null,
      exclude_stage: v.exclude_stage?.length ? v.exclude_stage : null,
      search: v.search,
      project: v.project,
      project_id: v.project_id,
      limit: v.limit,
      offset: v.offset,
    }),
  });
  require_current_server(
    inv,
    Array.isArray(data.tickets) &&
      data.tickets.every((row: Body) => has(row, 'needs')) &&
      (scopes.length > 0 || has(data.page, 'not_shown')),
  );
  if (v.as_json) return emit(data, true, '');
  const tickets: Body[] = data.tickets;
  const page: Body = data.page;
  const output: string[] = [];
  if (tickets.length) {
    for (const ticket of tickets) {
      output.push(
        scalar(ticket.title),
        `  ${format_recap(ticket)}`,
        `  status: ${ticket_status_label(ticket.ticket_status)} | Worker: ${scalar(ticket.agent_state)}`,
        `  stage: ${scalar(ticket.stage)} | needs: ${need_display(ticket.needs, ticket.ceiling_holder?.kind)}`,
        `  id: ${scalar(ticket.id)}` +
          (ticket.project ? ` | project: ${scalar(ticket.project)}` : ''),
      );
      if (ticket.worker_setup) output.push(`  setting up: ${setting_up_text(ticket.worker_setup)}`);
    }
  } else if (numeric_value(page.match_count)) output.push('No results at this offset.');
  output.push(`${plural(page.match_count, ...SCOPE_NOUNS[scope])}.`);
  const backlog = numeric_value(page.not_shown?.backlog) ?? 0;
  const done = numeric_value(page.not_shown?.done) ?? 0;
  if (backlog || done)
    output.push(`Not shown: ${backlog} in backlog, ${done} done. Add --backlog, --done, or --all.`);
  if (!page.complete) output.push(...paging_lines(page));
  return raw(output.join('\n') + '\n');
}
async function list_managers(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  if (v.settled && v.all) fail_validation(inv, 'use only one of --settled or --all');
  const data = await send(inv, 'GET', '/api/managers', {
    params: drop_none({
      detail: 'summary',
      settled: v.all ? null : !!v.settled,
      search: v.search,
      project: v.project,
      project_id: v.project_id,
      limit: v.limit,
      offset: v.offset,
    }),
  });
  require_current_server(
    inv,
    Array.isArray(data.managers) &&
      data.managers.every((row: Body) => has(row, 'ticket_needs')) &&
      (v.settled || v.all || has(data.page, 'not_shown')),
  );
  if (v.as_json) return emit(data, true, '');
  const managers: Body[] = data.managers;
  const page: Body = data.page;
  const output: string[] = [];
  if (managers.length) {
    const table = columns(
      managers.map((row) => [
        scalar(row.id),
        scalar(row.title) + (row.settled ? ' | settled' : ''),
        need_counts_display(row.ticket_needs),
      ]),
    );
    managers.forEach((row, index) => output.push(table[index], `  ${format_recap(row)}`));
  } else
    output.push(numeric_value(page.match_count) ? 'No results at this offset.' : 'No matches.');
  const settled = numeric_value(page.not_shown?.settled) ?? 0;
  if (settled) output.push(`Not shown: ${settled} settled. Add --settled or --all.`);
  if (!page.complete) output.push(...paging_lines(page));
  return raw(output.join('\n') + '\n');
}
const MANAGER_PARTS = ['goal', 'recap'];
async function manager_show(inv: Invocation): Promise<CommandResult> {
  const v = inv.values;
  const positional = [v.manager_id, ...(v.part_names ?? [])].filter(
    (word): word is string => word != null,
  );
  // The parts are a closed set, so a first word that is a part name is not a Manager id.
  const first_part = positional[0]?.split(',')[0];
  const named = positional.length && ![...MANAGER_PARTS, 'page'].includes(first_part!);
  const own = caller(inv);
  const id: string | undefined = named
    ? positional.shift()
    : own?.kind === 'manager'
      ? own.id
      : undefined;
  if (id === undefined)
    return fail_validation(inv, 'manager id required: pass it or run as a Manager');
  const requested = record_parts(inv, positional);
  if (requested?.includes('page'))
    fail_validation(inv, 'page is not a part of manager show', {
      recovery: 'panels page show --manager MANAGER_ID',
    });
  const data = await send(inv, 'GET', '/api/managers', { params: { detail: 'full', id } });
  require_current_server(inv, has(data, 'ticket_needs') && has(data, 'goals'));
  const header: Body = {};
  for (const key of ['id', 'title', 'project_id', 'project'])
    if (key in data) header[key] = data[key];
  header.settled = data.settled;
  for (const key of ['page_revision', 'page_character_count'])
    if (key in data) header[key] = data[key];
  if (data.manager != null)
    Object.assign(header, {
      manager_agent_key: data.manager.agent_key,
      manager_conversation_id: data.manager.conversation_id,
      employee_backend: data.manager.launch_configuration.employee_backend,
      employee_launch_model: data.manager.launch_configuration.employee_launch_model,
      employee_launch_reasoning_effort:
        data.manager.launch_configuration.employee_launch_reasoning_effort,
    });
  const goals: Body[] = response_field(inv, data, 'goals');
  // Exactly one held goal supplies the page the browser shows, instead of the Manager document.
  if (goals.length === 1) {
    const goal = goals[0];
    Object.assign(header, {
      visible_page: `held goal ${scalar(goal.title)} (${scalar(goal.id)})`,
      page_read: `panels goal show ${scalar(goal.id)}`,
      page_edit: `panels goal edit ${scalar(goal.id)}`,
    });
  } else
    Object.assign(header, {
      visible_page: 'own document',
      page_read: `panels page show --manager ${id}`,
      page_edit: `panels page edit --manager ${id}`,
    });
  header.held_goals = goals.length
    ? goals.map((goal) => `${scalar(goal.title)} (${scalar(goal.id)})`).join('; ')
    : 'none';
  header.tickets = need_counts_display(response_field(inv, data, 'ticket_needs'));
  const parts: RecordParts = new Map([
    ['goal', part(data.goal)],
    ['recap', part(data.recap)],
  ]);
  return emit_record(inv, header, parts, positional);
}
async function review(inv: Invocation): Promise<CommandResult> {
  const data = await send(inv, 'GET', '/api/review');
  require_current_server(inv, has(data, 'available') && has(data, 'cards'));
  if (!data.available) {
    const message = 'Review is not available right now.';
    throw new CliFailure({
      exit_code: 1,
      stdout: '',
      stderr: inv.values.as_json
        ? json({ error: { code: 'review_unavailable', message, detail: {} } }) + '\n'
        : message + '\n',
    });
  }
  if (inv.values.as_json) return emit(data, true, '');
  const cards: Body[] = data.cards;
  if (!cards.length) return raw('Nothing waits on Khushal.\n');
  const label = (card: Body) =>
    card.kind === 'chief'
      ? 'Chief'
      : `${card.kind === 'manager' ? 'Manager' : 'Ticket'} ${JSON.stringify(scalar(card.title))}`;
  const table = columns(
    cards.map((card, index) => [
      String(index + 1),
      label(card),
      (card.waits as string[]).join(', '),
      card.waiting_since == null ? '-' : age_text(card.waiting_since),
    ]),
  );
  const total = data.total ?? cards.length;
  const head =
    numeric_value(total) === 1
      ? '1 card waits on Khushal, oldest first.'
      : `${scalar(total)} cards wait on Khushal, oldest first.`;
  return raw([head, ...table].join('\n') + '\n');
}
async function recap(inv: Invocation): Promise<CommandResult> {
  const self = caller(inv);
  if (self === null)
    return fail_validation(inv, 'recap needs an agent identity (PLAN_ACTOR and its id)', {
      no_route: 'No panels call writes a recap without an agent identity.',
    });
  const text = await read_text(inv, { inline: 'message', label: 'recap' });
  const json_body = { recap: text };
  const data =
    self.kind === 'chief'
      ? await send(inv, 'PUT', '/api/workers/chief-of-staff/recap', { json_body })
      : await send(
          inv,
          'PATCH',
          `/api/${self.kind === 'ticket' ? 'tickets' : 'managers'}/${quote(self.id)}`,
          { json_body },
        );
  return emit(data, inv.values.as_json, 'recap written');
}
/** One message as text: who sent it and what it says. */
function message_text(payload: Body): string {
  if (typeof payload.text === 'string') return payload.text;
  return ((payload.content ?? []) as Body[])
    .map((piece) =>
      piece.piece === 'text' ? String(piece.text) : `[${String(piece.file_name ?? piece.piece)}]`,
    )
    .join('\n');
}
const MESSAGE_KINDS = ['prompt', 'agent_message', 'message_to_owner'];
function history_text(tid: string, events: Body[], has_more: boolean, after: unknown): string {
  const ordered = [...events].sort(
    (a, b) => (numeric_value(a.sequence) ?? 0) - (numeric_value(b.sequence) ?? 0),
  );
  const messages = ordered.filter((event) => MESSAGE_KINDS.includes(event.kind));
  const output = messages.map((event) => {
    const time = new Date((numeric_value(event.created_at) ?? 0) * 1000)
      .toISOString()
      .slice(0, 16)
      .replace('T', ' ');
    const sender =
      event.kind === 'agent_message' ? 'Worker' : scalar(event.payload.sender_label ?? 'unknown');
    const body = message_text(event.payload).replace(/\n/g, '\n  ');
    return `#${scalar(event.sequence)} ${time} ${sender}: ${body}`;
  });
  if (!output.length) output.push(`${tid} has no messages on this page.`);
  const others = ordered.length - messages.length;
  if (others) output.push(`plus ${others} other events; use --json`);
  if (has_more && ordered.length)
    output.push(
      after != null
        ? `More follow. Use --after ${scalar(ordered.at(-1)!.sequence)}.`
        : `More came before. Use --before ${scalar(ordered[0].sequence)}.`,
    );
  return output.join('\n');
}
async function ticket_schema(inv: Invocation): Promise<CommandResult> {
  const { ticket_schema_document, ticket_schema_text } = await import('./ticket_schema.ts');
  const operations = inv.values.operation ? [inv.values.operation] : ['create', 'edit'];
  const document = ticket_schema_document(operations);
  return emit(document, inv.values.as_json, ticket_schema_text(document));
}

export async function run_work_command(inv: Invocation): Promise<CommandResult> {
  const path = inv.command.path.join(' ');
  const v = inv.values;
  const as_json = v.as_json;
  let data: Body;
  switch (path) {
    case 'send-message': {
      const selected =
        Number(!!v.owner) +
        Number(!!v.chief) +
        Number(v.ticket_id != null) +
        Number(v.manager_id != null);
      if (selected !== 1)
        fail_validation(
          inv,
          'send-message requires exactly one of --owner, --chief, --ticket, or --manager',
        );
      const text = await read_text(inv, { inline: 'message', label: 'message' });
      const target = v.owner
        ? { kind: 'owner', id: 'owner' }
        : v.chief
          ? { kind: 'chief', id: 'chief' }
          : v.ticket_id != null
            ? { kind: 'ticket', id: v.ticket_id }
            : { kind: 'manager', id: v.manager_id };
      data = await send(inv, 'POST', '/api/messages/send', {
        json_body: { target, message: text, mode: v.mode },
      });
      return emit(
        data,
        as_json,
        `message ${scalar(data.fate)}` +
          (data.fate === 'refused' ? `: ${scalar(data.refusal_reason)}` : ''),
      );
    }
    case 'recap':
      return recap(inv);
    case 'review':
      return review(inv);
    case 'worker-type list':
      data = await send(inv, 'GET', '/api/worker-types?supported=true');
      return emit(
        data,
        as_json,
        lines(data.worker_types, (item) => scalar(item.worker_type)),
      );
    case 'worker-type show':
      data = await send(inv, 'GET', `/api/worker-types/${v.worker_type}`);
      return emit(data, as_json, json(data, { indent: 2, ensure_ascii: false }));
    case 'worker-type save':
      data = await send(inv, 'POST', '/api/worker-types', {
        json_body: await read_json_input(inv, 'Worker type record'),
      });
      return emit(data, as_json, `${scalar(data.worker_type)} saved`);
    case 'worker-type delete':
      data = await send(inv, 'DELETE', `/api/worker-types/${quote(v.worker_type)}`);
      return emit(data, as_json, `${scalar(v.worker_type)} deleted`);
    case 'worker-type skill': {
      const patch = await skill_patch(inv);
      const worker_path = quote(v.worker_type);
      const definition = await send(inv, 'GET', `/api/worker-types/${worker_path}`);
      const name = definition.profile.specialist_skill;
      data = await send(inv, 'PATCH', `/api/skills/${quote(name)}`, { json_body: patch });
      if (as_json) data = (await send(inv, 'GET', `/api/workers/${worker_path}`)).settings;
      return emit(data, as_json, `${scalar(name)} saved`);
    }
    case 'skill list':
      data = await send(inv, 'GET', '/api/skills');
      return emit(
        data,
        as_json,
        lines(data.skills, (item) => scalar(item.name)),
      );
    case 'skill show':
      data = await send(inv, 'GET', `/api/skills/${quote(v.name)}`);
      return as_json ? emit(data, true, '') : raw(data.markdown_body);
    case 'skill edit':
      data = await send(inv, 'PATCH', `/api/skills/${quote(v.name)}`, {
        json_body: await skill_patch(inv),
      });
      return emit(data, as_json, `${scalar(data.name)} saved`);
    case 'skill apply': {
      const manifest = await read_json_input(inv, 'skill manifest');
      data = await send(inv, 'POST', '/api/skills/apply', { json_body: manifest });
      return emit(data, as_json, `applied ${scalar(data.skills.length)} skills`);
    }
    case 'skill delete':
      data = await send(inv, 'DELETE', `/api/skills/${quote(v.name)}`);
      return emit(data, as_json, `${scalar(data.name)} deleted`);
    case 'skill write-file': {
      const content = await read_text(inv, { inline: 'message', label: 'file text' });
      data = await send(inv, 'PUT', `/api/skills/${quote(v.name)}/files`, {
        json_body: { path: v.path, content },
      });
      return emit(data, as_json, `${scalar(data.name)}/${scalar(data.path)} written`);
    }
    case 'skill check': {
      data = await send(inv, 'GET', '/api/skills/stale');
      const refs = data.stale_references;
      const human = !refs.length
        ? 'every skill row names only things this build has'
        : refs
            .map(
              (item: Body) =>
                `${scalar(item.skill_name)}: ${scalar(item.kind)} ${scalar(item.reference)}`,
            )
            .join('\n') +
          `\n\n${scalar(data.skill_names.length)} skills hold something this build does not have`;
      return emit(data, as_json, human);
    }
    case 'project list':
      data = await send(inv, 'GET', '/api/projects', {
        params: { detail: 'summary', limit: v.limit, offset: v.offset },
      });
      return emit(
        data,
        as_json,
        paged(
          lines(data.projects, (row) => `${scalar(row.id)} ${scalar(row.name)}`),
          data.page,
        ),
      );
    case 'project show': {
      data = await send(inv, 'GET', '/api/projects', {
        params: { detail: 'full', id: v.project_id },
      });
      const header = Object.fromEntries(
        ['id', 'name', 'folder_path', 'repository_url', 'default_branch']
          .filter((key) => key in data)
          .map((key) => [key, data[key]]),
      );
      return emit_record(inv, header, new Map([['summary', part(data.summary)]]), v.part_names);
    }
    case 'project create':
      data = await send(inv, 'POST', '/api/projects', {
        json_body: drop_none({
          name: v.name,
          summary: v.summary,
          folder_path: v.folder_path,
          repository_url: v.repository_url,
          default_branch: v.default_branch,
        }),
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(data.name)}`);
    case 'project set': {
      const value = await read_text(inv, {
        inline: 'value',
        label: `${v.field} value`,
        clearable: true,
      });
      if (v.field === 'name' && value === null)
        fail_validation(inv, `${scalar(v.field)} cannot be cleared`);
      const field =
        v.field === 'folder-path'
          ? 'folder_path'
          : v.field === 'repository-url'
            ? 'repository_url'
            : v.field === 'default-branch'
              ? 'default_branch'
              : v.field;
      const nullable =
        v.field === 'folder-path' || v.field === 'repository-url' || v.field === 'default-branch';
      data = await send(inv, 'PATCH', `/api/projects/${v.project_id}`, {
        json_body: { [field]: nullable && value === null ? null : (value ?? '') },
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(v.field)} set`);
    }
    case 'machine list':
      data = await send(inv, 'GET', '/api/machines');
      return emit(
        data,
        as_json,
        data.machines
          .map(
            (entry: Body) =>
              `${scalar(entry.machine_id)} ${scalar(entry.name)} ${entry.available ? 'Available' : 'Away'}`,
          )
          .join('\n'),
      );
    case 'machine move-work': {
      if (
        [v.ticket_id != null, v.manager_id != null, v.chief, v.everything].filter(Boolean)
          .length !== 1
      )
        return fail_validation(
          inv,
          'Choose exactly one of --ticket, --manager, --chief or --everything',
        );
      let results: WorkMoveResult[];
      if (v.everything) {
        const result: WorkMoveBatchResult = await send(inv, 'POST', '/api/work-moves/everything', {
          json_body: { machine_id: v.machine_id },
        });
        data = result;
        results = result.results;
      } else {
        const body: WorkMoveRequest = {
          machine_id: v.machine_id,
          owner: v.chief
            ? { kind: 'chief' }
            : v.ticket_id != null
              ? { kind: 'ticket', id: v.ticket_id }
              : { kind: 'manager', id: v.manager_id },
        };
        const result: WorkMoveResult = await send(inv, 'POST', '/api/work-moves', {
          json_body: body,
        });
        data = result;
        results = [result];
      }
      const output = emit(
        data,
        as_json,
        results
          .map(
            (result) =>
              `${result.title}: ${result.error ? `${result.moved ? 'Moved, but ' : ''}${result.error}` : result.moved ? 'Moved' : 'Already on this machine'}`,
          )
          .join('\n'),
      );
      return results.some((result) => result.error !== null) ? { ...output, exit_code: 1 } : output;
    }
    case 'machine register':
      data = await send(inv, 'POST', '/api/machines', { json_body: { name: v.name } });
      return emit(
        data,
        as_json,
        `Machine ${scalar(data.machine_id)} registered. Its credential, shown once: ${scalar(data.credential)}`,
      );
    case 'machine join-command': {
      const { validate_server_url } = await import('../executor/mac/configuration.ts');
      let home_url: string;
      try {
        home_url = validate_server_url(v.home_url);
      } catch (error) {
        return fail_validation(inv, error instanceof Error ? error.message : 'Invalid home origin');
      }
      const metadata = await send(inv, 'GET', '/api/meta');
      let revision: string;
      try {
        revision = require_released_revision(metadata.app_sha);
      } catch (error) {
        return fail_validation(inv, (error as Error).message);
      }
      if (v.server_machine === (v.name != null))
        return fail_validation(inv, 'Give a machine name or --server-machine, not both');
      const grant = v.server_machine
        ? await send(inv, 'POST', `/api/machines/${quote(HOME_MACHINE_ID)}/credential`)
        : await send(inv, 'POST', '/api/machines', { json_body: { name: v.name } });
      const credential = grant.credential;
      let command: string;
      try {
        command = join_command(home_url, revision, String(grant.machine_id), credential);
      } catch (error) {
        return fail_validation(inv, (error as Error).message);
      }
      return raw(
        [
          "Use this same home's trusted HTTPS origin, or its local executor link socket on the home's own account. Check the address before you share the credential.",
          'On the new machine, use an authenticated GitHub CLI and run:',
          command,
          'At the private credential prompt, paste this value:',
          credential,
          'Keep this value private. Credential replacement revokes it.',
          'After the command, remove its temporary script: rm -- "$panels_join_script"',
          '',
        ].join('\n'),
      );
    }
    case 'machine replace-credential':
      data = await send(inv, 'POST', `/api/machines/${quote(v.machine_id)}/credential`);
      return emit(
        data,
        as_json,
        `Machine ${scalar(data.machine_id)} has a new credential, shown once: ${scalar(data.credential)}`,
      );
    case 'ticket create': {
      const body = await read_json_input(inv, 'Ticket create input');
      if (v.machine_id != null) {
        if ('machine_id' in body) fail_validation(inv, 'machine_id accepts one input source');
        body.machine_id = v.machine_id;
      }
      if (v.locale != null) {
        if ('locale' in body) fail_validation(inv, 'locale accepts one input source');
        body.locale = v.locale;
      }
      data = await send(inv, 'POST', '/api/tickets', { json_body: body });
      return emit(
        data,
        as_json,
        `${scalar(data.id)} ${scalar(data.stage)}\n${scalar(data.supervision_receipt)}`,
      );
    }
    case 'ticket schema':
      return ticket_schema(inv);
    case 'ticket show':
      return show_ticket(inv);
    case 'ticket propose': {
      const tid = own_ticket(inv);
      const body = await read_text(inv, { inline: 'message', label: 'proposal' });
      data = await send(inv, 'POST', `/api/tickets/${tid}/propose`, { json_body: { body } });
      return emit(data, as_json, `proposed on ${scalar(data.id)}`);
    }
    case 'ticket approve':
      return approve(inv);
    case 'ticket send-back':
      return send_back(inv);
    case 'ticket backlog':
    case 'ticket resume': {
      const action = path.split(' ')[1];
      data = await send(inv, 'POST', `/api/tickets/${v.ticket_id}/${action}`, { json_body: {} });
      return emit(data, as_json, action === 'backlog' ? 'Ticket backlogged.' : 'Ticket resumed.');
    }
    case 'ticket delete':
      data = await send(inv, 'DELETE', `/api/tickets/${v.ticket_id}`, {
        params: v.force ? { force: true } : undefined,
      });
      return emit(data, as_json, `${scalar(v.ticket_id)} permanently deleted`);
    case 'ticket list':
      return list_tickets(inv);
    case 'ticket edit': {
      const tid = ticket_id(inv, v.ticket_id);
      const body = await read_json_input(inv, 'Ticket edit input');
      if (!Object.keys(body).length)
        fail_validation(inv, 'Ticket edit input must contain at least one field');
      data = await send(inv, 'PATCH', `/api/tickets/${tid}`, { json_body: body });
      return emit(data, as_json, `${scalar(data.id)} edited`);
    }
    case 'ticket complete':
      data = await send(inv, 'POST', `/api/tickets/${v.ticket_id}/complete/${v.field}`, {
        json_body: { body: await read_text(inv, { inline: 'value', label: `${v.field} value` }) },
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(v.field)} completed`);
    case 'ticket history': {
      const tid = ticket_id(inv, v.ticket_id);
      if (v.after != null && v.before != null)
        fail_validation(inv, 'history reads one direction: --after or --before');
      const detail = await send(inv, 'GET', '/api/tickets', {
        params: { detail: 'full', id: tid },
      });
      const cid = detail.conversation_id;
      if (!cid)
        fail_validation(inv, 'the Ticket has no current Worker conversation', {
          no_route: 'No panels history call is available until a Worker conversation exists.',
        });
      const params =
        v.after != null
          ? { after: v.after, limit: v.limit }
          : drop_none({ limit: v.limit, before: v.before });
      const page = await send(inv, 'GET', `/api/conversation/conversations/${cid}/events`, {
        params,
      });
      data = { ticket_id: tid, conversation_id: cid, events: page.events, has_more: page.has_more };
      return emit(data, as_json, history_text(tid, page.events, !!page.has_more, v.after));
    }
    case 'ticket recheck-worker-cleanup':
      data = await send(inv, 'POST', `/api/tickets/${v.ticket_id}/recheck-worker-cleanup`, {
        json_body: {},
      });
      return emit(
        data,
        as_json,
        `Worker cleanup ${data.complete ? 'complete' : 'not proven'}: ${scalar(data.reason)}`,
      );
    case 'ticket restart-worker': {
      if ((v.backend == null) !== (v.model == null))
        fail_validation(inv, 'restart-worker needs --backend and --model together, or neither');
      const body =
        v.backend != null
          ? {
              employee_backend: v.backend,
              employee_launch_model: v.model,
              employee_launch_reasoning_effort: v.reasoning_effort,
            }
          : {};
      data = await send(inv, 'POST', `/api/tickets/${v.ticket_id}/restart-worker`, {
        json_body: body,
      });
      const configuration = data.employee_configuration;
      const launch = `${scalar(configuration.employee_backend)} ${scalar(configuration.employee_launch_model)}`;
      let human = data.started
        ? `${scalar(v.ticket_id)} restarted on ${scalar(launch)}`
        : data.delivery_fate === 'uncertain'
          ? `${scalar(v.ticket_id)} start delivery is uncertain. The Worker claim remains held.`
          : data.delivery_fate === 'refused'
            ? `${scalar(v.ticket_id)} start delivery was refused`
            : `${scalar(v.ticket_id)} did not start: ${scalar(data.not_started_because)}`;
      if (data.revision_restarted_in_fresh_conversation)
        human +=
          '\nThe revision continues in a fresh conversation. The old working context is gone, and only the revision feedback carries over.';
      return emit(data, as_json, human);
    }
    case 'ticket block':
    case 'ticket unblock': {
      data = await send(
        inv,
        path === 'ticket block' ? 'PUT' : 'DELETE',
        `/api/collections/blockers/${v.ticket_id}/${v.blocker_id}`,
      );
      return emit(
        data,
        as_json,
        path === 'ticket block'
          ? `${scalar(v.ticket_id)} blocked by ${scalar(v.blocker_id)}`
          : `${scalar(v.ticket_id)} unblocked from ${scalar(v.blocker_id)}`,
      );
    }
    case 'ticket assign': {
      if (!!v.chief === (v.manager_id != null))
        fail_validation(inv, 'use exactly one of --chief or --manager');
      const parent = v.chief
        ? { kind: 'chief', id: 'chief' }
        : { kind: 'manager', id: v.manager_id };
      data = await send(inv, 'PATCH', `/api/tickets/${v.ticket_id}`, {
        json_body: { manager: parent },
      });
      return emit(data, as_json, `${scalar(v.ticket_id)} assigned`);
    }
    case 'manager create': {
      const body: Body = {
        title: v.title,
        goal: v.goal_file != null ? await read_named_file(inv, v.goal_file) : '',
      };
      if (v.project == null && v.project_id == null)
        fail_validation(inv, 'project required: pass --project or --project-id');
      if (v.project != null) body.project = v.project;
      if (v.project_id != null) body.project_id = v.project_id;
      data = await send(inv, 'POST', '/api/managers', { json_body: body });
      let human = `Created manager ${scalar(data.id)}.`;
      if (data.startup == null) human += ' Idle (no goal).';
      else {
        const startup = data.startup;
        const fate = startup.fate;
        human += ` Startup ${scalar(fate)}.`;
        if (fate === 'queued' && startup.queue_position != null)
          human += ` Queue position: ${scalar(startup.queue_position)}.`;
        if (['refused', 'uncertain', 'failed'].includes(fate)) {
          const reason = startup.refusal_reason || startup.error;
          if (reason) human += ` ${scalar(reason)}`;
          human += `\nThe manager is saved. Inspect: panels manager show ${scalar(data.id)}`;
          human += ['uncertain', 'failed'].includes(fate)
            ? '\nCheck its conversation before any resend. Do not create it again.'
            : `\nRetry on this manager: panels send-message --manager ${scalar(data.id)} --message "<goal>"`;
        }
      }
      return emit(data, as_json, human);
    }
    case 'manager list':
      return list_managers(inv);
    case 'manager show':
      return manager_show(inv);
    case 'manager delete':
      if (!v.yes) fail_validation(inv, 'permanent deletion requires --yes');
      data = await send(inv, 'DELETE', `/api/managers/${v.manager_id}`);
      return emit(data, as_json, `${scalar(v.manager_id)} permanently deleted`);
    case 'manager set': {
      const value = await read_text(inv, {
        inline: 'value',
        label: `${v.field} value`,
        clearable: true,
      });
      if (['title', 'project', 'project-id'].includes(v.field) && value === null)
        fail_validation(inv, `${scalar(v.field)} cannot be cleared`);
      const field = v.field === 'project-id' ? 'project_id' : v.field;
      data = await send(inv, 'PATCH', `/api/managers/${v.manager_id}`, {
        json_body: { [field]: value },
      });
      return emit(data, as_json, `${scalar(data.id)} ${scalar(v.field)} set`);
    }
    case 'ticket request-recap':
    case 'manager request-recap': {
      const kind = path.split(' ')[0];
      const identity = kind === 'ticket' ? v.ticket_id : v.manager_id;
      data = await send(inv, 'POST', '/api/recap-requests', {
        json_body: { target: { kind, id: identity } },
      });
      return emit(data, as_json, `Recap requested from ${scalar(identity)}.`);
    }
    case 'manager settle':
      data = await send(inv, 'PATCH', `/api/managers/${v.manager_id}/settled`, {
        json_body: { settled: !v.active },
      });
      return emit(
        data,
        as_json,
        `${scalar(v.manager_id)} ${scalar(v.active ? 'active' : 'settled')}`,
      );
    case 'manager artifact list': {
      data = await send(inv, 'GET', `/api/managers/${v.manager_id}/workspace`);
      const artifacts = artifact_paths(data.artifacts).sort();
      return emit({ manager_id: v.manager_id, artifacts }, as_json, lines(artifacts, scalar));
    }
    case 'manager artifact read': {
      const content = await send_text(
        inv,
        'GET',
        `/files/managers/${v.manager_id}/artifacts/${quote(v.artifact_path, true)}`,
      );
      return as_json
        ? emit({ manager_id: v.manager_id, path: v.artifact_path, content }, true, content)
        : raw(content);
    }
    case 'manager artifact write':
      data = await send(
        inv,
        'PUT',
        `/files/managers/${v.manager_id}/artifacts/${quote(v.artifact_path, true)}`,
        {
          json_body: {
            content: await read_text(inv, {
              inline: 'message',
              label: 'artifact text',
              exact: true,
            }),
          },
        },
      );
      return emit(data, as_json, scalar(data.url));
    case 'manager artifact delete':
      data = await send(
        inv,
        'DELETE',
        `/files/managers/${v.manager_id}/artifacts/${quote(v.artifact_path, true)}`,
      );
      return emit(data, as_json, `${scalar(v.artifact_path)} deleted`);
    default:
      throw new Error(`No work command handler for ${path}`);
  }
}
function artifact_paths(entries: Body[]): string[] {
  const paths: string[] = [];
  for (const entry of entries) {
    if (typeof entry.opens === 'string' && entry.opens.startsWith('artifacts/'))
      paths.push(entry.opens.slice(10));
    paths.push(...artifact_paths(entry.children));
  }
  return paths;
}
