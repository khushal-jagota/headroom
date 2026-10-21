import type { TicketRecord } from './navigation/contracts';
import type { EffectiveNavigationView } from './navigation/knowledge';
import type { NavigationPublication } from './navigation/collection';
import type { TicketDetail } from './types';
import type { WorkerTypeManifest } from './lifecycle';
import { buildLifecycle, ticketStageVisualStateFor, type Lifecycle } from './lifecycle';
import { navigationAgentState, navigationKnownBoolean, navigationConversationSignal } from './navigation/presentation';
import { agentHoldsTicket } from './workAttentionPresentation';
import type { FieldStageVisualState } from './ui';

export type TicketSection = {
  readonly record: TicketRecord;
  readonly detail: TicketDetail;
  readonly lifecycle: Lifecycle;
  readonly publication: NavigationPublication;
};

/** Configuration describes controls; the stored row supplies category and current gate. */
export function ticketSectionConfigurationMatches(record:TicketRecord,configuration:WorkerTypeManifest | undefined):boolean {
  if(!configuration || configuration.worker_type!==record.value.worker_type) return false;
  const row=record.value;
  const stage=configuration.stages.find(stage=>stage.id===row.stage);
  return stage!==undefined && stage.gating_field===row.gating_field
    && stage.ownership_mode===row.stage_ownership && stage.is_terminal===row.terminal
    && (row.gating_field===null || configuration.fields.some(field=>field.id===row.gating_field));
}

/** Public identity/control admission does not promise a hash of private field bodies. */
export function ticketSectionDetailMatches(record:TicketRecord,detail:TicketDetail):boolean {
  const row=record.value;
  if(detail.id!==record.id || detail.worker_type!==row.worker_type || detail.stage!==row.stage
    || detail.ticket_status!==row.ticket_status || detail.ceiling!==row.ceiling
    || detail.ceiling_holder.kind!==row.ceiling_holder.kind || detail.ceiling_holder.id!==row.ceiling_holder.id
    || detail.is_backlogged!==row.is_backlogged || detail.blocked!==row.blocked
    || detail.manager_id!==row.manager_id || detail.conversation_id!==row.conversation_id) return false;
  const proposal=detail.pending_proposal;
  return proposal===null ? row.proposal_field===null && row.proposal_created_at===null
    : proposal.field===row.proposal_field && proposal.created_at===row.proposal_created_at && proposal.revision===row.proposal_revision;
}

export function admitTicketSection(view:EffectiveNavigationView | null | undefined,record:TicketRecord | undefined,
  detail:TicketDetail | undefined,configuration:WorkerTypeManifest | undefined):TicketSection | undefined {
  if(!view || !record || !detail || !configuration || !ticketSectionDetailMatches(record,detail)
    || !ticketSectionConfigurationMatches(record,configuration)) return undefined;
  return {record,detail,lifecycle:buildLifecycle(configuration),publication:view.publication};
}

/** The metadata/body pair stays intact, while exact conversation facts always reproject. */
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
