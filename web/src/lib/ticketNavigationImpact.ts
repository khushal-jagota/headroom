import type { CommandImpact, RecordImpact } from './navigation/commandEvidence';
import { recordImpactTouches as impactHas } from './navigation/commandEvidence';
import type { EffectiveNavigationView } from './navigation/knowledge';
import type { TicketRecord } from './navigation/contracts';

const sectionFields:readonly (keyof TicketRecord['value'])[]=['stage','gating_field','stage_ownership','terminal','ticket_status','ceiling','ceiling_holder','is_backlogged','blocked','proposal_field','proposal_created_at','proposal_revision','conversation_id','worker_step_claim_out','manager_id'];
export function ticketSectionImpact(impact:RecordImpact,record:TicketRecord | undefined):boolean {
  if(!record) return false;
  return impact.kind==='ticket' && impact.id===record.id && impactHas(impact,sectionFields)
    || impact.kind==='conversation' && impact.id===record.value.conversation_id && impactHas(impact,['attention']);
}
export function ticketMastheadImpact(impact:RecordImpact,record:TicketRecord | undefined,parentId:string | null,projectId:string | null):boolean {
  if(!record) return false;
  return impact.kind==='ticket' && impact.id===record.id && impactHas(impact,['title','worker_type','conversation_id','manager_id','project_id'])
    || impact.kind==='manager' && impact.id===parentId && impactHas(impact,['project_id'])
    || impact.kind==='project' && impact.id===projectId && impactHas(impact,['name'])
    || impact.kind==='conversation' && impact.id===record.value.conversation_id && impactHas(impact,['attention']);
}

export type TicketNavigationOperation='title'|'recap'|'blocker'|'scope'|'stage'|'backlog'|'conversation'|'private'|'configuration';

/** Potential canonical effects from the existing Ticket writers, before dispatch. */
export function ticketCommandImpact(view:EffectiveNavigationView | null | undefined,id:string,operation:TicketNavigationOperation):CommandImpact {
  const record=view?.publication.records.find(record=>record.kind==='ticket' && record.id===id);
  if(!view || !record || record.kind!=='ticket') return {complete:false,reason:'missing_ticket_source'};
  const fields:readonly (keyof TicketRecord['value'])[]=operation==='title' ? ['title','updated_at']
    : operation==='recap' ? ['recap','recap_preview','updated_at']
    : operation==='blocker' ? ['blocked','ticket_status','updated_at']
    : operation==='scope' ? ['ceiling','ceiling_holder','proposal_revision','ticket_status','updated_at']
    : operation==='backlog' ? ['is_backlogged','updated_at']
    : operation==='conversation' ? ['conversation_id','updated_at','ticket_status','worker_step_claim_out']
    : operation==='private' || operation==='configuration' ? ['updated_at']
    : ['stage','blocked','gating_field','stage_ownership','terminal','ceiling','ceiling_holder','proposal_field','proposal_created_at','proposal_revision','ticket_status','worker_step_claim_out','updated_at'];
  const records:RecordImpact[]=[{kind:'ticket',id,fields}];
  if(operation==='stage') {
    // Terminal progression can release other blocked rows. Edge names are not public;
    // every currently blocked row is therefore a justified potential affected row.
    for(const candidate of view.publication.records) if(candidate.kind==='ticket' && candidate.value.blocked && candidate.id!==id) {
      records.push({kind:'ticket',id:candidate.id,fields:['blocked','ticket_status','updated_at']});
    }
  }
  if(operation==='conversation' || operation==='stage') {
    if(record.value.conversation_id!==null) records.push({kind:'conversation',id:record.value.conversation_id,fields:'*'});
    for(const address of view.publication.records) if(address.kind==='address' && address.value.owner_kind==='ticket' && address.value.owner_id===id) records.push({kind:'address',id:address.id,fields:'*'});
    // New address/conversation identities reach mounted owner/count projections via
    // their existing owner's conversation_id dependency, not a fabricated future ID.
  }
  return {complete:true,records};
}
