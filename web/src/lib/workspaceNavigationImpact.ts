import type { RecordImpact, CommandImpact } from './navigation/commandEvidence';
import type { NavigationPresentation } from './navigation/presentation';
import type { OrchestratorPrincipal } from './types';
import { workspaceRailChildMessageConversations, type WorkspaceRail } from './workspaceRail';
import type { WorkItemTicketFacts } from './workItemPresentation';
import { orchestratorGroupDefaultOpen } from './orchestratorChildren';

import { recordImpactTouches as touches } from './navigation/commandEvidence';
/** Sidebar membership, grouping, order, previews and current/historical message discovery. */
export function workspaceRailImpact(impact:RecordImpact,presentation:NavigationPresentation | undefined,rail:WorkspaceRail,settledOpen:boolean):boolean {
  const items=[...rail.items,...(settledOpen ? rail.settledItems : [])];
  const settledMessages=settledOpen ? rail.settledItems.flatMap(item=>
    presentation?.globalCounts ? presentation.scopedRailMessagesByParent.get(item.id) ?? []
      : workspaceRailChildMessageConversations(presentation?.messagesByParent.get(item.id) ?? [],item,presentation?.tickets ?? [])) : [];
  const groupedTickets=new Set([
    ...rail.chiefGroups.flatMap(group=>group.cards).map(ticket=>ticket.id),
    ...items.flatMap(item=>item.groups.flatMap(group=>group.cards)).map(ticket=>ticket.id),
  ]);
  const visibleTickets=new Set([...groupedTickets,...settledMessages.map(message=>message.id)]);
  if(impact.kind==='ticket') {
    if(touches(impact,['is_backlogged','blocked','stage','ticket_status','orchestrator_kind','sprint_item_id'])) return true;
    return visibleTickets.has(impact.id) && touches(impact,['title','recap_preview','worker_type','updated_at','project_id','conversation_id','ceiling_holder','gating_field','stage_ownership']);
  }
  if(impact.kind==='orchestrator') return touches(impact,['settled']) || items.some(item=>item.id===impact.id)
    && touches(impact,['title','created_at','project_id','conversation_id']);
  if(impact.kind==='chief') return touches(impact,['label','conversation_id']);
  if(impact.kind==='project') return rail.items.some(item=>item.projectId===impact.id) && touches(impact,['name']);
  if(impact.kind==='conversation') {
    if(!touches(impact,['attention'])) return false;
    if(presentation?.globalCounts)return true;
    if(presentation?.chief?.conversation_id===impact.id || items.some(item=>item.conversationId===impact.id)) return true;
    if([...groupedTickets].some(id=>presentation?.ticketsById.get(id)?.conversation_id===impact.id)
      || settledMessages.some(message=>message.conversationId===impact.id)) return true;
    // A quiet historical address can acquire an unread row. Ask the same helper
    // as WorkspaceMessages: historical done/blocked Tickets remain eligible.
    return settledOpen && Boolean(presentation?.addressesByConversation.get(impact.id)?.some(address=>{
      if(address.value.owner_kind!=='ticket') return false;
      const ticket=presentation.ticketsById.get(address.value.owner_id);
      if(!ticket) return false;
      return !ticket.is_backlogged && rail.settledItems.some(item=>item.id===address.value.parent_id);
    }));
  }
  return settledOpen && impact.kind==='address';
}
export function workspaceHoverImpact(impact:RecordImpact,kind:'ticket'|'orchestrator'|'chief',id:string,
  conversationId:string | null | undefined,presentation:NavigationPresentation | undefined,showsFallbackCount=false):boolean {
  if(impact.kind===kind && impact.id===id) return impact.fields==='*' || touches(impact,['title','recap','worker_type','stage','ticket_status','blocked','conversation_id','gating_field','stage_ownership','ceiling_holder']);
  if(impact.kind==='conversation') return impact.id===conversationId && touches(impact,['attention']);
  if(showsFallbackCount && kind!=='ticket' && impact.kind==='ticket') {
    if(touches(impact,['orchestrator_kind','sprint_item_id'])) return true;
    const parent=kind==='chief' ? 'chief:chief' : `orchestrator:${id}`;
    return Boolean(presentation?.ticketsByParent.get(parent)?.some(ticket=>ticket.id===impact.id))
      && touches(impact,['is_backlogged','blocked','stage','ticket_status']);
  }
  if(kind==='ticket' && impact.kind==='orchestrator') return presentation?.rawTicketsById.get(id)?.value.sprint_item_id===impact.id && touches(impact,['title']);
  return false;
}
export function orchestratorHeaderImpact(impact:RecordImpact,id:string,presentation:NavigationPresentation | undefined):boolean {
  const item=presentation?.rawOrchestratorsById.get(id)?.value;
  if(impact.kind==='orchestrator') return impact.id===id && touches(impact,['title','project_id','settled','conversation_id']);
  if(impact.kind==='project') return item?.project_id===impact.id && touches(impact,['name']);
  return impact.kind==='conversation' && item?.conversation_id===impact.id && touches(impact,['attention']);
}
export function orchestratorChildrenImpact(impact:RecordImpact,principal:OrchestratorPrincipal,presentation:NavigationPresentation | undefined,
  rows:readonly WorkItemTicketFacts[],groups:readonly {key:string;tickets:readonly WorkItemTicketFacts[]}[],open:Readonly<Record<string,boolean>>):boolean {
  const selected=new Set(rows.map(row=>row.id));
  if(impact.kind==='ticket') {
    // A parent-link footprint does not encode the destination. Both currently
    // mounted parent lists can change; capture them before the request.
    if(touches(impact,['orchestrator_kind','sprint_item_id'])) return true;
    const row=presentation?.rawTicketsById.get(impact.id)?.value;
    if(!row) return impact.fields==='*' || Boolean(presentation?.globalCounts) && touches(impact,['stage','is_backlogged','terminal','created_at','ticket_status','blocked','ceiling_holder','gating_field','stage_ownership','conversation_id']);
    const belongs=principal.kind==='chief' ? row.orchestrator_kind==='chief' : row.sprint_item_id===principal.id;
    if(!belongs) return false;
    if(touches(impact,['stage','is_backlogged','terminal','created_at','orchestrator_kind','sprint_item_id'])) return true;
    if(!selected.has(impact.id)) return false;
    if(touches(impact,['ticket_status','blocked','ceiling_holder','gating_field','stage_ownership','conversation_id'])) return true;
    const visible=groups.some(group=>(open[group.key] ?? orchestratorGroupDefaultOpen(group.key)) && group.tickets.some(ticket=>ticket.id===impact.id));
    return visible && touches(impact,['title','updated_at']);
  }
  return impact.kind==='conversation' && (Boolean(presentation?.globalCounts) || rows.some(ticket=>ticket.conversation_id===impact.id)) && touches(impact,['attention']);
}

/** Body updates project the full recap and its concise preview. */
export const orchestratorEditImpact=(id:string,field:'title'|'recap'):CommandImpact=>({complete:true,records:[{
  kind:'orchestrator',id,fields:field==='recap' ? ['recap','recap_preview'] : [field],
}]});
export const orchestratorSettleImpact=(id:string):CommandImpact=>({complete:true,records:[{kind:'orchestrator',id,fields:['settled']}]});

/** Send/reset may replace its pointer and exact conversation facts; stable owner scopes observers before future ids exist. */
export function conversationOwnerImpact(presentation:NavigationPresentation | undefined,kind:'chief'|'orchestrator',id:string):CommandImpact {
  if(!presentation || (kind==='chief' ? !presentation.chief : !presentation.rawOrchestratorsById.has(id))) return {complete:false,reason:'missing_conversation_owner_source'};
  const records:RecordImpact[]=[kind==='chief' ? {kind:'chief',id,fields:['conversation_id']} : {kind:'orchestrator',id,fields:['conversation_id']}];
  const pointer=kind==='chief' ? presentation?.chief?.conversation_id : presentation?.rawOrchestratorsById.get(id)?.value.conversation_id;
  if(pointer) records.push({kind:'conversation',id:pointer,fields:'*'});
  for(const addresses of presentation?.addressesByConversation.values() ?? []) for(const address of addresses) {
    if(address.value.owner_kind===kind && address.value.owner_id===id) records.push({kind:'address',id:address.id,fields:'*'});
  }
  // New identities are dependencies of this stable owner's changed pointer; no fabricated future id.
  return {complete:true,records};
}
