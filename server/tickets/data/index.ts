/** Ticket transitions receive the caller's transaction and never open a second writer. */
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import type { ReadSql, TransactionSql, SqlValue } from '../../core/database/contracts.ts';
import { PlannerError, OWNER, CHIEF, legacyActor } from '../../core/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import { newId } from '../../core/ids.ts';
import { require_conversation_backend_key } from '../../conversation/contracts.ts';
import { configured_worker_type_registry } from '../../worker_types/configuration.ts';
import { validate_workflow } from '../../worker_types/registry.ts';
import {
  workflow_from_preset,
  workflow_from_row,
  workflow_to_json,
} from '../../worker_types/store.ts';
import type { TicketWorkflowDefinition } from '../../worker_types/contracts.ts';
import { capture_ticket_attention } from '../../notifications/attention.ts';
import { capture_gate, record_approval } from '../../attention/history.ts';
import { create_proposal_wake, create_worker_error_wake } from '../../manager_wakes/data.ts';
import * as blocks from '../../core/ticket_blocks.ts';
import * as admission from '../logic/admission.ts';
import * as machine from '../logic/machine.ts';
import * as codec from '../logic/fields_codec.ts';
import * as resolution from '../logic/resolution.ts';
import * as routing from '../logic/proposal_routing.ts';
import * as employee from '../logic/employee_configuration.ts';
import { resolve_ticket_locale } from '../logic/locale.ts';
import type { Decision } from '../logic/decisions.ts';
import type {
  Ticket,
  TicketEdit,
  TicketFieldApproval,
  PendingTicketProposal,
  EmployeeLaunchConfiguration,
  ResolvedTicketCreationContext,
  ReviewTransferNotice,
  TicketDeletion,
  WorkerStepClaim,
} from '../contracts.ts';
const equal = isDeepStrictEqual;
const compact = (value: unknown) => JSON.stringify(value);
export const principal_to_json = (p: Principal) => compact({ id: p.id, kind: p.kind });
function principal_from_json(raw: string): Principal {
  const value: unknown = JSON.parse(raw);
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    throw new Error('stored principal must be an object');
  const p = value as Record<string, unknown>,
    kind = String(p.kind),
    id = String(p.id);
  if (!['owner', 'chief', 'manager', 'ticket', 'goal'].includes(kind))
    throw new Error('unknown principal kind');
  if (!id || id !== id.trim())
    throw new Error('principal id must be non-empty and must not contain outer whitespace');
  if ((kind === 'owner' || kind === 'chief') && id !== kind)
    throw new Error(`${kind} principal id must be ${kind}`);
  return { kind, id } as Principal;
}
function stored_locale(raw: unknown): Ticket['locale'] {
  if (raw === 'vps' || raw === 'macos') return raw;
  throw new Error('invalid stored Ticket locale');
}
function stored_claim(raw: unknown): WorkerStepClaim {
  if (raw === 'none' || raw === 'out' || raw === 'errored') return raw;
  throw new Error('invalid stored worker claim');
}
const field_ids = (t: Ticket) => t.workflow.fields.map((f) => f.id);
const index = (w: TicketWorkflowDefinition, s: string) => {
  const i = w.stages.findIndex((x) => x.id === s);
  if (i < 0) throw new PlannerError('validation', 'stage outside the linear order', { stage: s });
  return i;
};
const terminal = (t: Ticket) => t.workflow.stages[index(t.workflow, t.stage)]!.is_terminal;
function position(w: TicketWorkflowDefinition, stage: string, ceiling: string, historical = false) {
  machine.validate_ticket_position(w, stage, ceiling, historical);
}
function holder(sql: ReadSql, p: Principal) {
  if (p.kind === 'manager' && !sql.get('SELECT 1 FROM managers WHERE id=?', [p.id]))
    throw new PlannerError('validation', 'ceiling holder does not exist', { holder: p });
}
function eligible(p: Principal, kind: string, id: string | null) {
  return (
    p.kind === 'owner' ||
    (kind === 'chief' ? p.kind === 'chief' : p.kind === 'manager' && p.id === id)
  );
}
function review_holder(
  sql: ReadSql,
  p: Principal,
  id: string,
  manager_kind: string,
  manager_id: string | null,
) {
  holder(sql, p);
  if (!eligible(p, manager_kind, manager_id))
    throw new PlannerError(
      'validation',
      "ceiling holder must be the user or the Ticket's direct manager; name an eligible holder explicitly",
      { holder: p, ticket_id: id },
    );
}
export const TICKET_STORED_COLUMNS = [
  'id',
  'title',
  'worker_type',
  'workflow_json',
  'employee_backend',
  'employee_launch_model',
  'employee_launch_reasoning_effort',
  'stage',
  'deadline',
  'project_id',
  'manager_id',
  'recap',
  'ceiling',
  'conversation_id',
  'field_values',
  'field_approvals',
  'created_at',
  'updated_at',
  'pending_proposal',
  'ceiling_holder',
  'worker_step_claim',
  'worker_step_claim_revision',
  'pending_proposal_revision',
  'manager_kind',
  'is_backlogged',
  'locale',
] as const;
type Stored = Record<string, unknown> & {
  id: string;
  title: string;
  workflow_json: string;
  field_values: string;
  field_approvals: string;
  pending_proposal: string | null;
  ceiling_holder: string;
  stage: string;
  ceiling: string;
  worker_step_claim: WorkerStepClaim;
  has_live_blocker: number;
};
export function _ticket_row(sql: ReadSql, id: string): Stored {
  const row = sql.get<Stored>(
    `SELECT ${TICKET_STORED_COLUMNS.map((c) => 'tickets.' + c).join(',')},CASE WHEN tickets.manager_id IS NOT NULL THEN managers.project_id ELSE tickets.project_id END effective_project_id,projects.name project_name,managers.title manager_title,EXISTS(SELECT 1 FROM ticket_blocks JOIN tickets blocker ON blocker.id=ticket_blocks.blocking_ticket_id WHERE ticket_blocks.blocked_ticket_id=tickets.id AND blocker.stage != 'done') has_live_blocker FROM tickets LEFT JOIN managers ON managers.id=tickets.manager_id LEFT JOIN projects ON projects.id=CASE WHEN tickets.manager_id IS NOT NULL THEN managers.project_id ELSE tickets.project_id END WHERE tickets.id=?`,
    [id],
  );
  if (!row) throw new PlannerError('not_found', 'ticket not found', { ticket_id: id });
  return row;
}
export function read_ticket(sql: ReadSql, id: string): Ticket {
  const r = _ticket_row(sql, id),
    workflow = workflow_from_row(r);
  position(workflow, r.stage, r.ceiling, true);
  const values = codec.values_from_json(
    r.field_values,
    workflow.fields.map((f) => f.id),
  );
  const pending = codec.proposal_from_json(r.pending_proposal);
  const status =
    r.worker_step_claim === 'errored'
      ? 'errored'
      : pending
        ? 'awaiting_approval'
        : r.worker_step_claim === 'out'
          ? 'agent'
          : r.has_live_blocker
            ? 'blocked'
            : 'empty';
  return {
    id: r.id,
    title: r.title,
    worker_type: r.worker_type as string | null,
    workflow,
    employee_backend: String(r.employee_backend),
    employee_launch_model: r.employee_launch_model as string | null,
    employee_launch_reasoning_effort: r.employee_launch_reasoning_effort as string | null,
    stage: r.stage,
    deadline: r.deadline as string | null,
    project_id: r.effective_project_id as string | null,
    project_name: r.project_name as string | null,
    manager_id: r.manager_id as string | null,
    manager_kind: String(r.manager_kind),
    manager_title: r.manager_title as string | null,
    recap: String(r.recap),
    ceiling: r.ceiling,
    ceiling_holder: principal_from_json(r.ceiling_holder),
    ticket_status: status,
    worker_step_claim: stored_claim(r.worker_step_claim),
    worker_step_claim_revision: Number(r.worker_step_claim_revision),
    conversation_id: r.conversation_id as string | null,
    field_values: values,
    field_approvals: codec.approvals_from_json(r.field_approvals, values),
    pending_proposal: pending,
    created_at: Number(r.created_at),
    updated_at: Number(r.updated_at),
    locale: stored_locale(r.locale),
    is_backlogged: !!r.is_backlogged,
    pending_proposal_revision: Number(r.pending_proposal_revision),
  };
}
export function load_ticket_for_write(sql: ReadSql, id: string, startup_audit = false): Ticket {
  const reason = sql.get<{
    integrity_error_reason: string | null;
  }>('SELECT integrity_error_reason FROM tickets WHERE id=?', [id]);
  if (!startup_audit && reason?.integrity_error_reason)
    throw new PlannerError('validation', 'Ticket integrity failure', {
      ticket_id: id,
      reason: reason.integrity_error_reason,
    });
  const t = read_ticket(sql, id);
  position(t.workflow, t.stage, t.ceiling, startup_audit);
  holder(sql, t.ceiling_holder);
  codec.validate_state(t.field_values, t.pending_proposal, t.stage, {
    worker_type_definition: t.workflow,
  });
  return t;
}
export function read_ticket_by_conversation_id(sql: ReadSql, id: string): Ticket {
  const rows = sql.all<{
    id: string;
  }>(
    'SELECT tickets.id FROM tickets JOIN ticket_conversations ON ticket_conversations.ticket_id=tickets.id WHERE ticket_conversations.conversation_id=? ORDER BY tickets.id',
    [id],
  );
  if (!rows.length)
    throw new PlannerError('not_found', 'no ticket owns this Employee session', {
      conversation_id: id,
    });
  if (rows.length > 1)
    throw new PlannerError('validation', 'multiple tickets own this Employee session', {
      conversation_id: id,
      ticket_ids: rows.map((r) => r.id),
    });
  return read_ticket(sql, rows[0]!.id);
}
function placement(
  sql: ReadSql,
  p: string | null,
  m: string | null,
  blockers: readonly string[] = [],
) {
  if (m !== null) {
    const row = sql.get<{
      project_id: string;
    }>('SELECT project_id FROM managers WHERE id=?', [m]);
    if (!row) throw new PlannerError('not_found', 'manager not found', { manager_id: m });
    if (p !== row.project_id)
      throw new PlannerError('validation', 'ticket placement does not match manager');
  }
  if (p !== null && !sql.get('SELECT 1 FROM projects WHERE id=?', [p]))
    throw new PlannerError('validation', 'invalid project_id', { project_id: p });
  for (const b of blockers)
    if (!sql.get('SELECT 1 FROM tickets WHERE id=?', [b]))
      throw new PlannerError(
        'ticket_block_invalid',
        'blocking_ticket_id must be an existing ticket',
        { blocking_ticket_id: b },
      );
}
function enabled(sql: ReadSql, backend: string, model: string) {
  require_conversation_backend_key(backend);
  const row = sql.get<{
    enabled: number;
  }>('SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?', [
    backend,
    model,
  ]);
  if (row && !row.enabled)
    throw new PlannerError('validation', `Model "${model}" is disabled. Choose an enabled model.`, {
      employee_backend: backend,
      employee_launch_model: model,
    });
}
export interface CreationOptions {
  title: string;
  principal: Principal;
  now: number;
  title_max_chars: number;
  worker_type: string | null;
  workflow?: TicketWorkflowDefinition | null;
  brief?: string | null;
  project_id?: string | null;
  deadline?: string | null;
  manager_id?: string | null;
  manager_kind?: string | null;
  locale?: string | null;
  employee_backend?: string | null;
  employee_launch_model?: string | null;
  employee_launch_reasoning_effort?: string | null;
  reasoning_effort_is_set?: boolean;
  model_source_is_historical?: boolean;
  blocked_by_ticket_ids?: string[] | null;
  stated_ceiling?: string | null;
  stated_holder?: Principal | null;
}
export function requested_ticket_launch_configuration(
  worker_type: string | null,
  o: Pick<
    CreationOptions,
    | 'employee_backend'
    | 'employee_launch_model'
    | 'employee_launch_reasoning_effort'
    | 'reasoning_effort_is_set'
  > = {},
): EmployeeLaunchConfiguration {
  if (worker_type === null && (!o.employee_backend || !o.employee_launch_model?.trim()))
    throw new PlannerError(
      'validation',
      'a custom Ticket without a preset requires an explicit backend and model',
    );
  const profile =
    worker_type === null
      ? null
      : configured_worker_type_registry().require(worker_type).worker_profile;
  return employee.launch_configuration_for_a_new_ticket({
    default_backend: profile?.default_backend ?? o.employee_backend!,
    default_model: profile?.default_model ?? o.employee_launch_model!,
    default_reasoning_effort: profile?.default_reasoning_effort ?? null,
    employee_backend: require_conversation_backend_key(
      o.employee_backend ?? profile!.default_backend,
    ),
    employee_launch_model: o.employee_launch_model ?? null,
    employee_launch_reasoning_effort: o.employee_launch_reasoning_effort ?? null,
    reasoning_effort_is_set: o.reasoning_effort_is_set ?? false,
  });
}
export function resolve_ticket_draft_context(
  sql: ReadSql,
  o: Omit<CreationOptions, 'title' | 'principal' | 'now' | 'title_max_chars'>,
): ResolvedTicketCreationContext {
  admission.validate_deadline(o.deadline ?? null);
  if (o.worker_type !== null) {
    admission.require_creatable_worker_type(o.worker_type);
    configured_worker_type_registry().require(o.worker_type);
  }
  const launch_configuration = requested_ticket_launch_configuration(o.worker_type, o);
  enabled(sql, launch_configuration.employee_backend, launch_configuration.employee_launch_model!);
  let p = o.project_id ?? null;
  const m = o.manager_id ?? null;
  if (m !== null && p === null)
    p =
      sql.get<{
        project_id: string;
      }>('SELECT project_id FROM managers WHERE id=?', [m])?.project_id ?? null;
  placement(sql, p, m, o.blocked_by_ticket_ids ?? []);
  const locale = resolve_ticket_locale(o.locale ?? null);
  return { locale, launch_configuration, project_id: p };
}
export function resolve_ticket_creation_context(
  sql: ReadSql,
  o: CreationOptions,
): ResolvedTicketCreationContext {
  admission.validate_title(o.title, o.title_max_chars);
  return resolve_ticket_draft_context(sql, o);
}
function proposal_wake(tx: TransactionSql, t: Ticket, now: number) {
  if (!t.pending_proposal || t.ceiling_holder.kind === 'owner') return;
  const target = t.ceiling_holder;
  if (target.kind !== 'chief' && target.kind !== 'manager') return;
  if (target.kind === 'manager' && !tx.get('SELECT 1 FROM managers WHERE id=?', [target.id]))
    return;
  create_proposal_wake(tx, {
    manager_id: target.kind === 'manager' ? target.id : '',
    target,
    ticket_id: t.id,
    proposal_revision: t.pending_proposal_revision,
    ticket_title: t.title,
    proposal_field: t.pending_proposal.field,
    now,
  });
}
export function create_ticket(tx: TransactionSql, o: CreationOptions): Ticket {
  const m = o.manager_id ?? null,
    kind = o.manager_kind || (m ? 'manager' : 'chief');
  if (!['chief', 'manager'].includes(kind) || (kind === 'manager') !== (m !== null))
    throw new PlannerError('validation', 'manager kind does not match assignment');
  admission.validate_title(o.title, o.title_max_chars);
  admission.validate_deadline(o.deadline ?? null);
  let workflow = o.workflow;
  if (!workflow) {
    if (o.worker_type === null)
      throw new PlannerError('validation', 'a Ticket requires a preset or custom workflow');
    workflow = workflow_from_preset(configured_worker_type_registry().require(o.worker_type));
  } else {
    validate_workflow(workflow);
    if (workflow.specialist_skill !== null)
      throw new PlannerError('validation', 'a custom workflow cannot select a specialist skill');
  }
  const id = newId('t'),
    context = resolve_ticket_creation_context(tx, o),
    initial = workflow.stages[0]!.id;
  let stage = initial;
  const p = o.stated_holder ?? o.principal;
  review_holder(tx, p, id, kind, m);
  const ceiling =
    o.stated_ceiling == null ? initial : machine.resolve_ceiling(workflow, o.stated_ceiling);
  admission.validate_creation_ceiling(ceiling);
  const values: Record<string, string> = {},
    approvals: Record<string, TicketFieldApproval> = {};
  let proposal: PendingTicketProposal | null = null;
  if (o.brief?.trim() && workflow.fields.some((f) => f.id === 'brief')) {
    if (index(workflow, initial) < index(workflow, ceiling)) {
      stage = workflow.stages[1]!.id;
      values.brief = o.brief;
      approvals.brief = { method: 'auto_accept', principal: null, display_label: 'Auto-accepted' };
    } else
      proposal = {
        field: 'brief',
        body: o.brief,
        proposed_by: legacyActor(o.principal),
        created_at: o.now,
      };
  }
  const launch = context.launch_configuration;
  tx.run(
    "INSERT INTO tickets (id,title,worker_type,workflow_json,employee_backend,employee_launch_model,employee_launch_reasoning_effort,stage,deadline,project_id,manager_id,manager_kind,recap,ceiling,ceiling_holder,conversation_id,field_values,field_approvals,pending_proposal,pending_proposal_revision,created_at,updated_at,locale) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, '',?,?,NULL,?,?,?,?,?,?,?)",
    [
      id,
      o.title,
      o.worker_type,
      workflow_to_json(workflow),
      launch.employee_backend,
      launch.employee_launch_model,
      launch.employee_launch_reasoning_effort,
      stage,
      o.deadline ?? null,
      m === null ? context.project_id : null,
      m,
      kind,
      ceiling,
      principal_to_json(p),
      codec.values_to_json(values),
      codec.approvals_to_json(approvals),
      codec.proposal_to_json(proposal),
      Number(proposal !== null),
      o.now,
      o.now,
      context.locale,
    ],
  );
  proposal_wake(tx, read_ticket(tx, id), o.now);
  for (const b of o.blocked_by_ticket_ids ?? []) blocks.add_ticket_block(tx, b, id, o.now);
  capture_ticket_attention(tx, id, o.now);
  return load_ticket_for_write(tx, id);
}
export function _apply_decision(
  tx: TransactionSql,
  t: Ticket,
  d: Decision,
  now: number,
  o: {
    newly_filed_proposal?: boolean;
  } = {},
): Ticket {
  holder(tx, d.ceiling_holder);
  position(t.workflow, d.stage, d.ceiling);
  codec.validate_state(d.field_values, d.pending_proposal, d.stage, {
    worker_type_definition: t.workflow,
  });
  const blocked = tx.all<{
    blocked_ticket_id: string;
  }>(
    'SELECT blocked_ticket_id FROM ticket_blocks WHERE blocking_ticket_id=? ORDER BY blocked_ticket_id',
    [t.id],
  );
  if ((t.stage !== 'done') !== (d.stage !== 'done') && d.stage !== 'done')
    for (const b of blocked)
      if (blocks.would_create_active_ticket_block_cycle(tx, t.id, b.blocked_ticket_id))
        throw new PlannerError(
          'ticket_block_cycle',
          'ticket stage change would activate a Ticket block cycle',
          { blocking_ticket_id: t.id, blocked_ticket_id: b.blocked_ticket_id },
        );
  const stored = _ticket_row(tx, t.id);
  tx.run(
    'UPDATE tickets SET field_values=?,field_approvals=?,pending_proposal=?,pending_proposal_revision=pending_proposal_revision+?,stage=?,ceiling=?,ceiling_holder=?,updated_at=? WHERE id=?',
    [
      codec.replace_declared_values(stored.field_values, d.field_values, field_ids(t)),
      codec.replace_declared_approvals(stored.field_approvals, d.field_approvals, field_ids(t)),
      codec.proposal_to_json(d.pending_proposal),
      Number(!!o.newly_filed_proposal && d.pending_proposal !== null),
      d.stage,
      d.ceiling,
      principal_to_json(d.ceiling_holder),
      now,
      t.id,
    ],
  );
  if (d.stage !== t.stage) {
    tx.run('DELETE FROM ticket_paired_stage_openers WHERE ticket_id=?', [t.id]);
    tx.run('DELETE FROM ticket_revision_feedback WHERE ticket_id=?', [t.id]);
  }
  if ((t.stage !== 'done') !== (d.stage !== 'done'))
    for (const b of blocked) {
      if (d.stage === 'done')
        tx.run('DELETE FROM ticket_blocks WHERE blocking_ticket_id=? AND blocked_ticket_id=?', [
          t.id,
          b.blocked_ticket_id,
        ]);
      blocks.touch_blocked_ticket(tx, b.blocked_ticket_id, now);
    }
  capture_ticket_attention(tx, t.id, now);
  return read_ticket(tx, t.id);
}
export function _write_worker_step_claim(
  tx: TransactionSql,
  id: string,
  claim: WorkerStepClaim,
  now: number,
  o: {
    error_reason?: string;
    notify_supervisor?: boolean;
  } = {},
): void {
  const before = load_ticket_for_write(tx, id);
  tx.run(
    'UPDATE tickets SET worker_step_claim=?,updated_at=?,worker_step_claim_revision=CASE WHEN worker_step_claim=? THEN worker_step_claim_revision ELSE worker_step_claim_revision+1 END WHERE id=?',
    [claim, now, claim, id],
  );
  if (o.notify_supervisor !== false && before.worker_step_claim !== claim && claim === 'errored') {
    const target =
      before.manager_kind === 'chief'
        ? CHIEF
        : before.manager_id && tx.get('SELECT 1 FROM managers WHERE id=?', [before.manager_id])
          ? { kind: 'manager' as const, id: before.manager_id }
          : null;
    if (target)
      create_worker_error_wake(tx, {
        target,
        ticket_id: id,
        claim_revision: before.worker_step_claim_revision + 1,
        ticket_title: before.title,
        error_reason: o.error_reason!,
        now,
      });
  }
  capture_ticket_attention(tx, id, now);
}
function give_back(tx: TransactionSql, t: Ticket, now: number) {
  if (t.worker_step_claim !== 'none') _write_worker_step_claim(tx, t.id, 'none', now);
}
export function file_current_proposal(
  tx: TransactionSql,
  id: string,
  o: {
    body: string;
    principal: Principal;
    now: number;
  },
): Ticket {
  if (o.principal.kind !== 'ticket' || o.principal.id !== id)
    throw new PlannerError(
      'agent_forbidden',
      "only the Ticket's own Worker can file its proposal",
      { ticket_id: id },
    );
  const t = load_ticket_for_write(tx, id),
    parked = routing.parked_destination(
      routing.route_proposal({
        stage: t.stage,
        ceiling: t.ceiling,
        holder: t.ceiling_holder,
        worker_type_definition: t.workflow,
      }),
    );
  const d = resolution.decide_file_proposal(t, {
    body: o.body,
    principal: o.principal,
    now: o.now,
    parked_destination: parked,
    worker_type_definition: t.workflow,
  });
  const updated = _apply_decision(tx, t, d, o.now, { newly_filed_proposal: true });
  proposal_wake(tx, updated, o.now);
  give_back(tx, updated, o.now);
  return load_ticket_for_write(tx, id);
}
function explicit_approval(sql: ReadSql, p: Principal): TicketFieldApproval {
  let name =
    p.kind === 'owner'
      ? 'Khushal'
      : p.kind === 'chief'
        ? 'Chief'
        : sql.get<{
            title: string;
          }>('SELECT title FROM managers WHERE id=?', [p.id])?.title;
  if (!name) throw new PlannerError('not_found', 'approving manager not found');
  return { method: 'explicit_accept', principal: p, display_label: `Approved by ${name}` };
}
export interface AcceptOptions {
  field?: string;
  principal: Principal;
  now: number;
  edited_body?: string | null;
  next_ceiling?: string | null;
  next_holder: Principal;
  expected_proposal_revision?: number | null;
}
export function accept_proposal(
  tx: TransactionSql,
  id: string,
  fieldOrOptions: string | AcceptOptions,
  options?: AcceptOptions,
): Ticket {
  const o = typeof fieldOrOptions === 'string' ? options! : fieldOrOptions,
    field = typeof fieldOrOptions === 'string' ? fieldOrOptions : o.field!;
  const t = load_ticket_for_write(tx, id);
  if (
    o.expected_proposal_revision != null &&
    t.pending_proposal_revision !== o.expected_proposal_revision
  )
    throw new PlannerError(
      'validation',
      'the visible proposal changed; read it again before accepting',
      { ticket_id: id },
    );
  const d = resolution.decide_accept(t, {
    field,
    edited_body: o.edited_body ?? null,
    next_ceiling: o.next_ceiling ?? null,
    next_holder: o.next_holder,
    worker_type_definition: t.workflow,
    approval: explicit_approval(tx, o.principal),
  });
  review_holder(tx, o.next_holder, id, t.manager_kind, t.manager_id);
  const u = _apply_decision(tx, t, d, o.now);
  record_approval(tx, {
    ticket_id: id,
    field,
    principal: o.principal,
    method: 'explicit_accept',
    now: o.now,
  });
  give_back(tx, u, o.now);
  return load_ticket_for_write(tx, id);
}
export function complete_user_owned_gate(
  tx: TransactionSql,
  id: string,
  field: string,
  o: {
    new_body: string;
    principal: Principal;
    now: number;
  },
): Ticket {
  const t = load_ticket_for_write(tx, id),
    d = resolution.decide_complete_user_owned_gate(t, {
      field,
      new_body: o.new_body,
      principal: o.principal,
      worker_type_definition: t.workflow,
    });
  const u = _apply_decision(tx, t, d, o.now);
  if (d.stage !== t.stage) give_back(tx, u, o.now);
  return load_ticket_for_write(tx, id);
}
export function require_reject(
  sql: ReadSql,
  id: string,
  o: {
    principal: Principal;
    has_guidance: boolean;
  },
): Ticket {
  const t = load_ticket_for_write(sql, id);
  resolution.decide_reject(t, {
    principal: o.principal,
    has_guidance: o.has_guidance,
    worker_type_definition: t.workflow,
  });
  return t;
}
export function reject_proposal(
  tx: TransactionSql,
  id: string,
  o: {
    message: string | null;
    principal: Principal;
    now: number;
    expected_proposal?: PendingTicketProposal | null;
  },
): Ticket {
  if (o.message !== null) admission.validate_revision_guidance(o.message);
  const t = load_ticket_for_write(tx, id);
  if (o.expected_proposal && !equal(t.pending_proposal, o.expected_proposal))
    throw new PlannerError('validation', 'proposal changed before revision guidance was applied', {
      ticket_id: id,
      field: t.workflow.stages[index(t.workflow, t.stage)]!.gating_field,
    });
  const d = resolution.decide_reject(t, {
    principal: o.principal,
    has_guidance: o.message !== null,
    worker_type_definition: t.workflow,
  });
  if (o.message !== null) {
    const feedback = compact([
      { sender_kind: o.principal.kind, sender_id: o.principal.id, message: o.message },
    ]).replace(/[\u007f-\uffff]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
    tx.run(
      'INSERT INTO ticket_revision_feedback (ticket_id,stage,feedback_json,revision,created_at,updated_at) VALUES (?,?,?,1,?,?) ON CONFLICT(ticket_id) DO UPDATE SET stage=excluded.stage,feedback_json=excluded.feedback_json,revision=ticket_revision_feedback.revision+1,updated_at=excluded.updated_at',
      [id, t.stage, feedback, o.now, o.now],
    );
  }
  const u = _apply_decision(tx, t, d, o.now);
  tx.run('DELETE FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?', [id, t.stage]);
  give_back(tx, u, o.now);
  return load_ticket_for_write(tx, id);
}
export function set_ticket_backlog(
  tx: TransactionSql,
  id: string,
  o: {
    is_backlogged: boolean;
    principal: Principal;
    now: number;
  },
): Ticket {
  const t = load_ticket_for_write(tx, id);
  if (terminal(t))
    throw new PlannerError('validation', 'terminal Tickets cannot change backlog state');
  if (t.is_backlogged !== o.is_backlogged)
    tx.run('UPDATE tickets SET is_backlogged=?,updated_at=? WHERE id=?', [
      Number(o.is_backlogged),
      o.now,
      id,
    ]);
  return load_ticket_for_write(tx, id);
}
function name(sql: ReadSql, p: Principal): string {
  if (p.kind === 'owner') return 'The user';
  if (p.kind === 'chief') return 'The Chief';
  return sql.get<{
    title: string;
  }>(`SELECT title FROM ${p.kind === 'manager' ? 'managers' : 'tickets'} WHERE id=?`, [p.id])!
    .title;
}
function transfer(
  sql: ReadSql,
  before: Ticket,
  after: Ticket,
  actor: Principal,
  notices?: ReviewTransferNotice[],
) {
  if (
    !before.pending_proposal ||
    !equal(before.pending_proposal, after.pending_proposal) ||
    equal(before.ceiling_holder, after.ceiling_holder)
  )
    return;
  const other = !equal(actor, after.ceiling_holder);
  notices?.push({
    ticket_id: after.id,
    ticket_title: after.title,
    previous_holder: before.ceiling_holder,
    new_holder: after.ceiling_holder,
    new_holder_name: name(sql, after.ceiling_holder),
    notify_previous_holder: other && !equal(actor, before.ceiling_holder),
    mover_name: other ? name(sql, actor) : null,
  });
}
function release_holder(tx: TransactionSql, id: string, now: number) {
  const t = read_ticket(tx, id);
  if (eligible(t.ceiling_holder, t.manager_kind, t.manager_id)) return;
  tx.run('UPDATE tickets SET ceiling_holder=?,updated_at=? WHERE id=?', [
    principal_to_json(OWNER),
    now,
    id,
  ]);
  capture_ticket_attention(tx, id, now);
}
export function edit_ticket(
  tx: TransactionSql,
  id: string,
  o: {
    edit: TicketEdit;
    title_max_chars: number;
    principal: Principal;
    now: number;
    review_transfer_notices?: ReviewTransferNotice[];
  },
): Ticket {
  const t = load_ticket_for_write(tx, id),
    e = o.edit;
  const title = e.title ?? t.title,
    deadline = 'deadline' in e ? e.deadline! : t.deadline;
  let manager_id = 'manager_id' in e ? e.manager_id! : t.manager_id,
    manager_kind = t.manager_kind;
  if (e.manager) {
    if ('manager_id' in e) throw new PlannerError('validation', 'use only manager for assignment');
    admission.validate_manager(e.manager);
    manager_kind = e.manager.kind;
    manager_id = e.manager.kind === 'manager' ? e.manager.id : null;
  } else if ('manager_id' in e) manager_kind = manager_id === null ? 'chief' : 'manager';
  const old = tx.get<{
    project_id: string | null;
  }>('SELECT project_id FROM tickets WHERE id=?', [id])!.project_id;
  let effective: string | null, stored: string | null;
  if (manager_id !== null) {
    const m = tx.get<{
      project_id: string;
    }>('SELECT project_id FROM managers WHERE id=?', [manager_id]);
    if (!m) throw new PlannerError('not_found', 'manager not found', { manager_id });
    effective = m.project_id;
    if ('project_id' in e && e.project_id !== effective)
      throw new PlannerError('validation', 'ticket placement does not match manager');
    stored = null;
  } else {
    effective = 'project_id' in e ? e.project_id! : t.project_id;
    stored = effective;
  }
  admission.validate_title(title, o.title_max_chars);
  admission.validate_deadline(deadline);
  placement(tx, effective, manager_id);
  const changes: Record<string, SqlValue> = {};
  for (const [key, before, after] of [
    ['title', t.title, title],
    ['deadline', t.deadline, deadline],
    ['project_id', old, stored],
    ['manager_id', t.manager_id, manager_id],
    ['manager_kind', t.manager_kind, manager_kind],
  ] as const)
    if (before !== after) changes[key] = after;
  if ('recap' in e) changes.recap = e.recap!;
  if (e.field_values && Object.keys(e.field_values).length) {
    let d = t;
    for (const [field, body] of Object.entries(e.field_values)) {
      const decision = resolution.decide_edit_settled_field(d, {
        field,
        new_body: body,
        worker_type_definition: t.workflow,
      });
      d = { ...d, field_values: decision.field_values, field_approvals: decision.field_approvals };
    }
    const row = _ticket_row(tx, id);
    changes.field_values = codec.replace_declared_values(
      row.field_values,
      d.field_values,
      field_ids(t),
    );
    changes.field_approvals = codec.replace_declared_approvals(
      row.field_approvals,
      d.field_approvals,
      field_ids(t),
    );
  }
  if ('ceiling' in e) {
    const d = resolution.decide_set_ceiling(t, {
      ceiling: e.ceiling!,
      worker_type_definition: t.workflow,
    });
    if (d.ceiling !== t.ceiling) {
      position(t.workflow, t.stage, d.ceiling);
      changes.ceiling = d.ceiling;
    }
  }
  if (e.ceiling_holder) {
    review_holder(tx, e.ceiling_holder, id, manager_kind, manager_id);
    if (!equal(e.ceiling_holder, t.ceiling_holder))
      changes.ceiling_holder = principal_to_json(e.ceiling_holder);
  }
  const readdress = !!t.pending_proposal && 'ceiling_holder' in changes;
  if (readdress) changes.pending_proposal_revision = t.pending_proposal_revision + 1;
  if (!Object.keys(changes).length) return t;
  tx.run(
    `UPDATE tickets SET ${Object.keys(changes)
      .map((k) => k + '=?')
      .join(',')},updated_at=? WHERE id=?`,
    [...Object.values(changes), o.now, id],
  );
  if (!e.ceiling_holder && 'manager_id' in changes) release_holder(tx, id, o.now);
  if ('ceiling' in e || 'ceiling_holder' in e) capture_ticket_attention(tx, id, o.now);
  const u = load_ticket_for_write(tx, id);
  if (readdress) proposal_wake(tx, u, o.now);
  transfer(tx, t, u, o.principal, o.review_transfer_notices);
  return u;
}
export function classify_ticket(
  tx: TransactionSql,
  id: string,
  o: {
    manager_id: string;
    principal: Principal;
    now: number;
    review_transfer_notices?: ReviewTransferNotice[];
  },
): Ticket {
  const t = load_ticket_for_write(tx, id);
  if (!tx.get('SELECT project_id FROM managers WHERE id=?', [o.manager_id]))
    throw new PlannerError('not_found', 'manager not found', { manager_id: o.manager_id });
  if (t.manager_id === o.manager_id) return t;
  tx.run(
    "UPDATE tickets SET manager_id=?,manager_kind='manager',project_id=NULL,updated_at=? WHERE id=?",
    [o.manager_id, o.now, id],
  );
  release_holder(tx, id, o.now);
  const u = load_ticket_for_write(tx, id);
  transfer(tx, t, u, o.principal, o.review_transfer_notices);
  return u;
}
export function unclassify_ticket(
  tx: TransactionSql,
  id: string,
  o: {
    manager_id: string;
    principal: Principal;
    now: number;
    review_transfer_notices?: ReviewTransferNotice[];
  },
): Ticket {
  const t = load_ticket_for_write(tx, id),
    m = tx.get<{
      project_id: string;
    }>('SELECT project_id FROM managers WHERE id=?', [o.manager_id]);
  if (!m) throw new PlannerError('not_found', 'manager not found', { manager_id: o.manager_id });
  if (t.manager_id === null) return t;
  if (t.manager_id !== o.manager_id)
    throw new PlannerError('validation', 'Ticket has a different Manager');
  tx.run(
    "UPDATE tickets SET manager_id=NULL,manager_kind='chief',project_id=?,updated_at=? WHERE id=?",
    [m.project_id, o.now, id],
  );
  release_holder(tx, id, o.now);
  const u = load_ticket_for_write(tx, id);
  transfer(tx, t, u, o.principal, o.review_transfer_notices);
  return u;
}
export function employee_launch_configuration(t: Ticket): EmployeeLaunchConfiguration {
  return {
    employee_backend: t.employee_backend,
    employee_launch_model: t.employee_launch_model,
    employee_launch_reasoning_effort: t.employee_launch_reasoning_effort,
  };
}
export function employee_configuration_editable(t: Ticket): boolean {
  return t.conversation_id === null && t.worker_step_claim !== 'out';
}
export function write_employee_configuration(
  tx: TransactionSql,
  id: string,
  o: EmployeeLaunchConfiguration & {
    now: number;
  },
): Ticket {
  enabled(tx, o.employee_backend, o.employee_launch_model!);
  const t = load_ticket_for_write(tx, id),
    current = employee_launch_configuration(t),
    d = employee.normalize_employee_launch_configuration(current, o);
  if (equal(current, d)) return t;
  if (!employee_configuration_editable(t))
    throw new PlannerError(
      'already_running',
      'Employee configuration is frozen while a conversation or a worker step holds it',
      { ticket_id: id },
    );
  tx.run(
    'UPDATE tickets SET employee_backend=?,employee_launch_model=?,employee_launch_reasoning_effort=?,updated_at=? WHERE id=?',
    [d.employee_backend, d.employee_launch_model, d.employee_launch_reasoning_effort, o.now, id],
  );
  return load_ticket_for_write(tx, id);
}
export function write_ticket_conversation_start(
  tx: TransactionSql,
  id: string,
  o: {
    conversation_id: string;
    backend: string;
    model: string | null;
    reasoning_effort: string | null;
    now: number;
  },
): Ticket {
  load_ticket_for_write(tx, id);
  if (
    tx.run(
      'UPDATE tickets SET conversation_id=?,updated_at=? WHERE id=? AND conversation_id IS NULL',
      [o.conversation_id, o.now, id],
    ).changes
  ) {
    tx.run('INSERT INTO ticket_conversations (conversation_id,ticket_id) VALUES (?,?)', [
      o.conversation_id,
      id,
    ]);
    capture_ticket_attention(tx, id, o.now);
  }
  return load_ticket_for_write(tx, id);
}
export function remove_ticket_conversation_start(
  tx: TransactionSql,
  id: string,
  o: {
    conversation_id: string;
    now: number;
  },
): void {
  load_ticket_for_write(tx, id);
  tx.run('UPDATE tickets SET conversation_id=NULL,updated_at=? WHERE id=? AND conversation_id=?', [
    o.now,
    id,
    o.conversation_id,
  ]);
  tx.run('DELETE FROM ticket_conversations WHERE ticket_id=? AND conversation_id=?', [
    id,
    o.conversation_id,
  ]);
  capture_ticket_attention(tx, id, o.now);
}
export function clear_ticket_conversation_link(
  tx: TransactionSql,
  id: string,
  o: {
    expected_conversation_id: string;
    now: number;
  },
): boolean {
  load_ticket_for_write(tx, id);
  const changed = !!tx.run(
    'UPDATE tickets SET conversation_id=NULL,updated_at=? WHERE id=? AND conversation_id=?',
    [o.now, id, o.expected_conversation_id],
  ).changes;
  if (changed) capture_ticket_attention(tx, id, o.now);
  return changed;
}
export function write_ticket_last_chosen_configuration(
  tx: TransactionSql,
  id: string,
  o: {
    expected_conversation_id: string;
    model: string | null;
    reasoning_effort: string | null;
    now: number;
    employee_backend?: string | null;
  },
): boolean {
  load_ticket_for_write(tx, id);
  return !!tx.run(
    'UPDATE tickets SET employee_backend=COALESCE(?,employee_backend),employee_launch_model=?,employee_launch_reasoning_effort=?,updated_at=? WHERE id=? AND conversation_id=?',
    [
      o.employee_backend ?? null,
      o.model,
      o.reasoning_effort,
      o.now,
      id,
      o.expected_conversation_id,
    ],
  ).changes;
}
export function release_worker_step_claim(
  tx: TransactionSql,
  id: string,
  o: {
    expected_claim: WorkerStepClaim;
    expected_claim_revision: number;
    now: number;
  },
): boolean {
  const t = load_ticket_for_write(tx, id);
  if (
    t.worker_step_claim !== o.expected_claim ||
    t.worker_step_claim_revision !== o.expected_claim_revision
  )
    return false;
  _write_worker_step_claim(tx, id, 'none', o.now);
  return true;
}
export function mark_worker_step_claim_errored(
  tx: TransactionSql,
  id: string,
  o: {
    expected_claim: WorkerStepClaim;
    expected_claim_revision: number;
    now: number;
    error_reason: string;
    notify_supervisor?: boolean;
  },
): boolean {
  const t = load_ticket_for_write(tx, id);
  if (
    t.worker_step_claim !== o.expected_claim ||
    t.worker_step_claim_revision !== o.expected_claim_revision
  )
    return false;
  _write_worker_step_claim(tx, id, 'errored', o.now, o);
  return true;
}
export function mark_ticket_errored(
  tx: TransactionSql,
  id: string,
  o: {
    now: number;
    error_reason: string;
  },
): Ticket {
  load_ticket_for_write(tx, id);
  _write_worker_step_claim(tx, id, 'errored', o.now, o);
  return load_ticket_for_write(tx, id);
}
export function delete_ticket_records(
  tx: TransactionSql,
  id: string,
  o: {
    now: number;
    even_while_running?: boolean;
    capture_attention?: boolean;
  },
): TicketDeletion {
  const t = tx.get<{
    title: string;
    worker_step_claim: string;
    manager_id: string | null;
  }>('SELECT title,worker_step_claim,manager_id FROM tickets WHERE id=?', [id]);
  if (!t) throw new PlannerError('not_found', 'ticket not found', { ticket_id: id });
  if (!o.even_while_running && t.worker_step_claim === 'out')
    throw new PlannerError('already_running', 'ticket activity is still running', {
      ticket_id: id,
    });
  const rows = tx.all<{
    blocking_ticket_id: string;
    blocked_ticket_id: string;
  }>(
    'SELECT blocking_ticket_id,blocked_ticket_id FROM ticket_blocks WHERE blocking_ticket_id=? OR blocked_ticket_id=? ORDER BY blocking_ticket_id,blocked_ticket_id',
    [id, id],
  );
  const linked = [
    ...new Set(
      rows.map((r) => (r.blocking_ticket_id === id ? r.blocked_ticket_id : r.blocking_ticket_id)),
    ),
  ].sort();
  for (const r of rows) {
    tx.run('DELETE FROM ticket_blocks WHERE blocking_ticket_id=? AND blocked_ticket_id=?', [
      r.blocking_ticket_id,
      r.blocked_ticket_id,
    ]);
    if (r.blocking_ticket_id === id)
      blocks.touch_blocked_ticket(tx, r.blocked_ticket_id, o.now, {
        capture_attention: o.capture_attention ?? true,
      });
  }
  capture_gate(tx, { ticket_id: id, active: false, now: o.now });
  tx.run('DELETE FROM tickets WHERE id=?', [id]);
  for (const table of ['notification_attention_edges', 'notification_attention_state'])
    tx.run(`DELETE FROM ${table} WHERE subject_kind='ticket' AND subject_id=?`, [id]);
  return {
    ticket_id: id,
    title: t.title,
    manager_ids: t.manager_id === null ? [] : [t.manager_id],
    linked_ticket_ids: linked,
  };
}
export function delete_ticket(
  tx: TransactionSql,
  id: string,
  o: {
    principal: Principal;
    now: number;
    even_while_running?: boolean;
  },
): TicketDeletion {
  load_ticket_for_write(tx, id);
  return delete_ticket_records(tx, id, o);
}
export function ticket_record_sha256(sql: ReadSql, id: string): string {
  const row = sql.get<Record<string, unknown>>(
    `SELECT ${TICKET_STORED_COLUMNS.map((c) => 'tickets.' + c).join(',')},tickets.integrity_error_reason FROM tickets WHERE id=?`,
    [id],
  );
  if (!row) throw new PlannerError('not_found', 'ticket not found', { ticket_id: id });
  return createHash('sha256')
    .update(
      compact(
        Object.fromEntries(
          Object.keys(row)
            .sort()
            .map((k) => [k, row[k]]),
        ),
      ),
    )
    .digest('hex');
}
export function take_worker_step_claim(
  tx: TransactionSql,
  id: string,
  o: {
    now: number;
    readiness_check: (
      sql: ReadSql,
      ticket: Ticket,
      options: {
        worker_type_definition: TicketWorkflowDefinition;
      },
    ) => boolean;
  },
): Ticket | null {
  const t = load_ticket_for_write(tx, id);
  if (!o.readiness_check(tx, t, { worker_type_definition: t.workflow })) return null;
  const ownership = t.workflow.stages[index(t.workflow, t.stage)]!.ownership_mode;
  if (ownership === null)
    throw new PlannerError('validation', 'a terminal stage has no worker step to claim', {
      ticket_id: id,
      stage: t.stage,
    });
  if (ownership === 'user')
    tx.run('INSERT INTO ticket_paired_stage_openers(ticket_id,stage,opened_at) VALUES (?,?,?)', [
      id,
      t.stage,
      o.now,
    ]);
  _write_worker_step_claim(tx, id, 'out', o.now);
  return load_ticket_for_write(tx, id);
}
export function forget_user_stage_opener(
  tx: TransactionSql,
  id: string,
  o: {
    stage: string;
  },
): void {
  tx.run('DELETE FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?', [id, o.stage]);
}
export function restore_interrupted_worker_step_claim(
  tx: TransactionSql,
  id: string,
  o: {
    conversation_id: string;
    expected_claim_revision: number;
    now: number;
  },
): number | null {
  const t = load_ticket_for_write(tx, id);
  if (
    t.conversation_id !== o.conversation_id ||
    t.worker_step_claim !== 'errored' ||
    t.worker_step_claim_revision !== o.expected_claim_revision
  )
    return null;
  _write_worker_step_claim(tx, id, 'out', o.now);
  return o.expected_claim_revision + 1;
}
const structural: Record<string, string> = {
  'unknown worker type': 'PlannerError: unknown worker type (worker_type)',
  'stage outside the linear order': 'PlannerError: stage outside the linear order (stage)',
  "ceiling outside the type's range": "PlannerError: ceiling outside the type's range (ceiling)",
  'ceiling holder does not exist': 'PlannerError: ceiling holder does not exist (ceiling_holder)',
  'a Ticket cannot hold its own ceiling':
    'PlannerError: a Ticket cannot hold its own ceiling (ceiling_holder)',
  'corrupt ticket field state': 'PlannerError: corrupt ticket field state (fields or proposal)',
  'unknown agent backend': 'PlannerError: unknown agent backend (employee_backend)',
};
export function audit_ticket_registry_integrity(
  tx: TransactionSql,
  o: {
    startup?: boolean;
    worker_type?: string;
  } = {},
): PlannerError[] {
  const rows = o.worker_type
    ? tx.all<{
        id: string;
        integrity_error_reason: string | null;
      }>('SELECT id,integrity_error_reason FROM tickets WHERE worker_type=? ORDER BY id', [
        o.worker_type,
      ])
    : tx.all<{
        id: string;
        integrity_error_reason: string | null;
      }>('SELECT id,integrity_error_reason FROM tickets ORDER BY id');
  const issues: PlannerError[] = [];
  for (const row of rows) {
    let reason: string | null = null;
    try {
      const t = load_ticket_for_write(tx, row.id, !!o.startup);
      require_conversation_backend_key(t.employee_backend);
    } catch (error) {
      if (!o.startup)
        throw new Error(`ticket integrity audit failed: id=${row.id} reason=${String(error)}`);
      reason =
        error instanceof PlannerError
          ? (structural[error.message] ?? 'PlannerError: invalid stored Ticket structure')
          : error instanceof SyntaxError
            ? 'JSONDecodeError: malformed stored JSON'
            : error instanceof TypeError
              ? 'TypeError: invalid stored Ticket member type'
              : 'ValueError: invalid stored Ticket member value';
    }
    if (o.startup && row.integrity_error_reason !== reason) {
      tx.run('UPDATE tickets SET integrity_error_reason=? WHERE id=?', [reason, row.id]);
      if (reason)
        issues.push(
          new PlannerError('validation', 'Ticket integrity failure', { ticket_id: row.id, reason }),
        );
    }
  }
  return issues;
}
export const claim_ticket_for_worker_step = take_worker_step_claim;
