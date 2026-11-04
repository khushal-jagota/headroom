import type { TicketRecord, TicketValue } from './navigation/contracts';
import type { EffectiveNavigationView } from './navigation/knowledge';
import type { TicketDetail } from './types';
import type { WorkerTypeManifest } from './lifecycle';
import { buildLifecycle, ticketStageVisualStateFor, type Lifecycle } from './lifecycle';
import { navigationAgentState, navigationKnownBoolean, navigationConversationSignal } from './navigation/presentation';
import { agentHoldsTicket } from './workAttentionPresentation';
import type { FieldStageVisualState } from './ui';

export type TicketSection = {
  readonly record: {readonly id:string; readonly value:Pick<TicketValue,
    'stage'|'gating_field'|'stage_ownership'|'terminal'|'ticket_status'|'ceiling'|'ceiling_holder'|
    'is_backlogged'|'blocked'|'conversation_id'|'proposal_field'|'proposal_created_at'|'proposal_revision'>};
  readonly detail: TicketDetail;
  readonly lifecycle: Lifecycle;
};

/** Render the loaded Ticket directly. The server decides whether an action is valid. */
export function ticketSection(detail:TicketDetail | undefined,configuration:WorkerTypeManifest | undefined):TicketSection | undefined {
  if(!detail || !configuration) return undefined;
  const lifecycle=buildLifecycle(configuration),stage=configuration.stages.find(stage=>stage.id===detail.stage);
  return {detail,lifecycle,record:{id:detail.id,value:{
    stage:detail.stage,gating_field:stage?.gating_field ?? null,stage_ownership:stage?.ownership_mode ?? null,
    terminal:stage?.is_terminal ?? false,ticket_status:detail.ticket_status ?? 'empty',
    ceiling:detail.ceiling,ceiling_holder:detail.ceiling_holder,is_backlogged:detail.is_backlogged ?? false,
    blocked:detail.blocked ?? false,conversation_id:detail.conversation_id,
    proposal_field:detail.pending_proposal?.field ?? null,proposal_created_at:detail.pending_proposal?.created_at ?? null,
    proposal_revision:detail.pending_proposal?.revision ?? 0,
  }}};
}

/** Compare the displayed body only for render observation, never for action admission. */
export function ticketSectionDetailMatches(record:TicketRecord,detail:TicketDetail):boolean {
  const row=record.value;
  if('integrity_error' in row) return false;
  if(detail.id!==record.id || detail.worker_type!==row.worker_type || detail.stage!==row.stage
    || detail.ticket_status!==row.ticket_status || detail.ceiling!==row.ceiling
    || detail.ceiling_holder.kind!==row.ceiling_holder.kind || detail.ceiling_holder.id!==row.ceiling_holder.id
    || detail.is_backlogged!==row.is_backlogged || detail.blocked!==row.blocked
    || detail.manager_id!==row.manager_id || detail.conversation_id!==row.conversation_id) return false;
  const proposal=detail.pending_proposal;
  return proposal===null ? row.proposal_field===null && row.proposal_created_at===null
    : proposal.field===row.proposal_field && proposal.created_at===row.proposal_created_at && proposal.revision===row.proposal_revision;
}

/** Conversation facts reproject independently from the loaded Ticket. */
export function ticketSectionFacts(section:TicketSection,view:EffectiveNavigationView | null | undefined) {
  const row=section.record.value;
  const knowledge=row.conversation_id===null ? undefined : view?.conversations.get(row.conversation_id);
  return {
    awaiting_approval:row.ticket_status==='awaiting_approval' && row.ceiling_holder.kind==='owner' && row.ceiling_holder.id==='owner',
    assigned:row.stage_ownership==='user',
    awaiting_reply:navigationKnownBoolean(knowledge?.message_for_owner),
    awaiting_answer:navigationKnownBoolean(knowledge?.awaiting_answer),
    agent_state:navigationAgentState(row.ticket_status,row.stage,knowledge),
    ticket_status:row.ticket_status,
    conversation_signal:navigationConversationSignal(view,row.conversation_id),
  };
}

export function ticketSectionHeading(section:TicketSection,view:EffectiveNavigationView | null | undefined):string {
  const row=section.record.value,facts=ticketSectionFacts(section,view);
  return row.blocked || row.ticket_status==='blocked' ? 'Blocked on'
    : facts.agent_state==='errored' ? 'Error working on'
      : facts.assigned ? 'Your step:' : row.stage==='needs_brief' ? 'Waiting for'
        : agentHoldsTicket(facts) ? 'Currently working on' : 'Waiting at';
}

export function ticketSectionRunLabel(section:TicketSection,view:EffectiveNavigationView | null | undefined):string | null {
  const row=section.record.value,facts=ticketSectionFacts(section,view);
  if(row.blocked || row.ticket_status==='blocked') return null;
  return facts.awaiting_approval ? 'awaiting approval' : facts.assigned ? "you're on it" : null;
}

export function ticketSectionFieldState(section:TicketSection,view:EffectiveNavigationView | null | undefined,field:string):FieldStageVisualState {
  const row=section.record.value,lc=section.lifecycle;
  const facts=ticketSectionFacts(section,view);
  const status=facts.awaiting_approval ? 'awaiting_approval' : facts.assigned ? 'assigned'
    : facts.agent_state==='errored' ? 'errored' : agentHoldsTicket(facts) ? 'agent' : row.ticket_status;
  return ticketStageVisualStateFor(lc,{ticketStage:row.stage,ticketStatus:status,fieldName:field,
    fieldHasProposal:row.proposal_field===field});
}
