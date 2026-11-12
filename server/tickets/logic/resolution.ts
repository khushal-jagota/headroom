import { OWNER, legacyActor, PlannerError, type Principal } from '../../core/contracts.ts';
import type { WorkflowDefinition } from '../../worker_types/contracts.ts';
import type { Ticket, TicketFieldApproval } from '../contracts.ts';
import { from_ticket, type Decision } from './decisions.ts';
import * as admission from './admission.ts';
import * as machine from './machine.ts';
import { same_principal } from './review_transfer.ts';
type Definition = {
    worker_type_definition: WorkflowDefinition;
};
function accept_gating(ticket: Ticket, field: string, body: string, next_ceiling: string | null, next_holder: Principal | null, d: WorkflowDefinition, approval: TicketFieldApproval | null = null): Decision { const target = machine.advance_target(d, ticket.stage); if (target === null)
    throw new PlannerError('validation', 'stage has no advance target'); const approvals = { ...ticket.field_approvals }; delete approvals[field]; if (approval !== null)
    approvals[field] = approval; return { ...from_ticket(ticket), field_values: { ...ticket.field_values, [field]: body }, field_approvals: approvals, pending_proposal: null, stage: target, ceiling: next_ceiling ?? ticket.ceiling, ceiling_holder: next_ceiling === null ? ticket.ceiling_holder : next_holder! }; }
export function decide_file_proposal(ticket: Ticket, o: Definition & {
    body: string;
    principal: Principal;
    now: number;
    parked_destination: Principal | null;
}): Decision { admission.validate_body(o.body, 'proposal body'); const field = machine.gating_field(o.worker_type_definition, ticket.stage); if (field === null)
    throw new PlannerError('validation', 'ticket stage has no proposal field'); admission.check_agent_proposal(ticket.stage, field, o); if (o.parked_destination === null)
    return accept_gating(ticket, field, o.body, null, null, o.worker_type_definition, { method: 'auto_accept', principal: null, display_label: 'Auto-accepted' }); return { ...from_ticket(ticket), pending_proposal: { field, body: o.body, proposed_by: legacyActor(o.principal), created_at: o.now }, ceiling_holder: o.parked_destination }; }
function pending(ticket: Ticket, field: string, d: WorkflowDefinition) { if (ticket.pending_proposal === null)
    throw new PlannerError('not_found', 'no pending proposal', { ticket_id: ticket.id }); if (field !== ticket.pending_proposal.field || field !== machine.gating_field(d, ticket.stage))
    throw new PlannerError('validation', 'proposal field is not the current gate', { field }); return ticket.pending_proposal; }
export function decide_accept(ticket: Ticket, o: Definition & {
    field: string;
    edited_body: string | null;
    next_ceiling: string | null;
    next_holder: Principal | null;
    approval: TicketFieldApproval;
}): Decision { if (o.edited_body !== null)
    admission.validate_body(o.edited_body, 'edit-accept text'); const proposal = pending(ticket, o.field, o.worker_type_definition); const target = machine.advance_target(o.worker_type_definition, ticket.stage); if (target === null)
    throw new PlannerError('validation', 'stage has no advance target'); const ceiling = machine.resolve_next_ceiling(target, o.next_ceiling, o); if (o.next_holder === null)
    throw new PlannerError('scope_missing', 'approval requires the holder for the next ceiling', { missing: ['next_holder'] }); return accept_gating(ticket, o.field, o.edited_body ?? proposal.body, ceiling, o.next_holder, o.worker_type_definition, o.approval); }
export function decide_complete_user_owned_gate(ticket: Ticket, o: Definition & {
    field: string;
    new_body: string;
    principal: Principal;
}): Decision { admission.validate_body(o.new_body, 'field value'); const d = o.worker_type_definition; const s = machine.stage_definition(d, ticket.stage); if (s.is_terminal)
    throw new PlannerError('validation', 'terminal tickets have no gate to complete', { field: o.field }); require_field(d, o.field); if (ticket.field_values[o.field] !== undefined)
    throw new PlannerError('validation', 'field is already settled', { field: o.field }); if (o.field !== s.gating_field)
    throw new PlannerError('validation', 'only the current user-owned gate can be completed', { field: o.field, gating_field: s.gating_field, stage: ticket.stage }); if (machine.stage_ownership_mode(ticket.stage, o) !== 'user')
    throw new PlannerError('agent_forbidden', 'only a user-owned stage can be completed with a direct value', { field: o.field, stage: ticket.stage }); if (ticket.pending_proposal !== null)
    throw new PlannerError('validation', 'a pending proposal must be resolved before direct completion', { field: o.field }); if (ticket.worker_step_claim === 'out')
    throw new PlannerError('already_running', 'ticket control is active', { ticket_id: ticket.id, worker_step_claim: ticket.worker_step_claim }); const target = machine.advance_target(d, ticket.stage); if (target === null)
    throw new PlannerError('validation', 'stage has no advance target'); return accept_gating(ticket, o.field, o.new_body, target, o.principal, d); }
function require_field(d: WorkflowDefinition, field: string): void { if (!d.fields.some(f => f.id === field))
    throw new PlannerError('validation', 'unknown ticket field', { field }); }
export function decide_edit_settled_field(ticket: Ticket, o: Definition & {
    field: string;
    new_body: string;
}): Decision { admission.validate_body(o.new_body, 'field value'); require_field(o.worker_type_definition, o.field); if (ticket.field_values[o.field] === undefined)
    throw new PlannerError('validation', 'field has no settled value to edit; a Stage still waiting for its answer is completed, not edited', { field: o.field }); if (!machine.field_is_passed(o.field, ticket.stage, o))
    throw new PlannerError('validation', 'field is not yet passed', { field: o.field }); const approvals = { ...ticket.field_approvals }; if (ticket.field_values[o.field] !== o.new_body)
    delete approvals[o.field]; return { ...from_ticket(ticket), field_values: { ...ticket.field_values, [o.field]: o.new_body }, field_approvals: approvals }; }
export function decide_reject(ticket: Ticket, o: Definition & {
    principal: Principal;
    has_guidance: boolean;
}): Decision { if (ticket.worker_step_claim === 'out')
    throw new PlannerError('already_running', 'the ticket worker is already revising this proposal'); if (machine.stage_definition(o.worker_type_definition, ticket.stage).is_terminal)
    throw new PlannerError('validation', 'terminal tickets cannot be rejected'); if (o.has_guidance && ticket.conversation_id === null)
    throw new PlannerError('validation', 'ticket has no existing worker session'); const field = machine.gating_field(o.worker_type_definition, ticket.stage); if (field === null)
    throw new PlannerError('validation', 'ticket has no approval item to reject'); pending(ticket, field, o.worker_type_definition); return { ...from_ticket(ticket), pending_proposal: null, ceiling_holder: same_principal(o.principal, OWNER) ? OWNER : ticket.ceiling_holder }; }
export function decide_set_ceiling(ticket: Ticket, o: Definition & {
    ceiling: string;
}): Decision { const d = o.worker_type_definition; const ceiling = machine.resolve_ceiling(d, o.ceiling); if (ticket.pending_proposal !== null && machine.stage_index(d, ceiling) > Math.max(machine.stage_index(d, ticket.stage), machine.stage_index(d, ticket.ceiling)))
    throw new PlannerError('validation', 'a proposal is pending; use explicit accept to raise the ceiling past its stage', { ticket_id: ticket.id }); return { ...from_ticket(ticket), ceiling }; }
export function decide_set_ceiling_holder(ticket: Ticket, holder: Principal): Decision { return { ...from_ticket(ticket), ceiling_holder: holder }; }
