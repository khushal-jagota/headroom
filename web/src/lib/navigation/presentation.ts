import type { TicketValue, TicketRecord, OrchestratorRecord, ProjectRecord, AddressRecord } from './contracts';
import type { EffectiveNavigationView, KnownFact, ConversationKnowledge } from './knowledge';
import type { AgentState } from '../types';
import type { WorkspaceTicket, WorkspaceOrchestrator, WorkspaceChief } from '../workspaceRail';
import type { ConversationSignal } from '../conversationSignal';
import type { WorkspaceMessageConversation } from '../types';

export type NavigationTicket = WorkspaceTicket & Pick<TicketValue,'created_at' | 'terminal' | 'is_backlogged'>;
export type NavigationWorkspacePresentation = {
  readonly tickets: NavigationTicket[];
  readonly orchestrators: WorkspaceOrchestrator[];
  readonly chief: WorkspaceChief | null;
  readonly messages: WorkspaceMessageConversation[];
  readonly conversationSignals: ReadonlyMap<string,Partial<ConversationSignal>>;
};
const value = (fact: KnownFact<boolean> | undefined): boolean | null => fact && fact.state !== 'unknown' ? fact.value : null;
function signal(knowledge: ConversationKnowledge | undefined): Partial<ConversationSignal> {
  const working=value(knowledge?.working), message=value(knowledge?.message_for_owner);
  return {...(working === null ? {} : {working}),...(message === null ? {} : {message_for_owner:message})};
}
/** Same existing agent-state precedence; unknown runtime never becomes an idle fact. */
function agentState(status: string | null, stage: string | null, knowledge: ConversationKnowledge | undefined): AgentState | null {
  const working=value(knowledge?.working), failed=value(knowledge?.last_turn_failed);
  if (working === true) return 'working';
  if (status === 'errored') return 'errored';
  if (failed === true && stage !== 'done') return 'errored';
  return working === false && (failed === false || stage === 'done') ? 'idle' : null;
}

/** One coherent source view, including the canonical bounded preview strings. */
export function navigationWorkspacePresentation(view: EffectiveNavigationView): NavigationPresentation {
  const records=view.publication.records;
  const conversationSignals=new Map([...view.conversations].map(([id,knowledge])=>[id,signal(knowledge)]));
  const rawTicketsById=new Map<string,TicketRecord>();
  const rawOrchestratorsById=new Map<string,OrchestratorRecord>();
  const rawProjectsById=new Map<string,ProjectRecord>();
  const addressesByConversation=new Map<string,AddressRecord[]>();
  const reviewItems: import('../types').NavigationReviewItem[]=[];
  const currentConversationIds=new Set<string>();
  let exactOutClaimCount=0;
  let chiefRecord:Extract<(typeof records)[number],{kind:'chief'}> | undefined;
  for(const record of records) {
    if(record.kind==='ticket' || record.kind==='orchestrator' || record.kind==='chief') {
      if(record.value.conversation_id!==null) currentConversationIds.add(record.value.conversation_id);
    }
    if(record.kind==='ticket') {
      const row=record.value;
      if(row.worker_step_claim_out) exactOutClaimCount+=1;
      if(row.ticket_status==='awaiting_approval' && row.ceiling_holder.kind==='owner' && row.ceiling_holder.id==='owner'
        && row.gating_field!==null && row.proposal_field!==null && row.proposal_created_at!==null) {
        reviewItems.push({review_item_type:'proposal',ticket_id:record.id,field:row.gating_field,
          title:row.title,waiting_since:row.proposal_created_at,proposal_field:row.proposal_field,proposal_revision:row.proposal_revision});
      }
    }
    if(record.kind==='ticket') rawTicketsById.set(record.id,record);
    else if(record.kind==='orchestrator') rawOrchestratorsById.set(record.id,record);
    else if(record.kind==='project') rawProjectsById.set(record.id,record);
    else if(record.kind==='chief') chiefRecord=record;
    else if(record.kind==='address') {const addresses=addressesByConversation.get(record.value.conversation_id) ?? [];addresses.push(record);addressesByConversation.set(record.value.conversation_id,addresses);}
  }
  const projects=new Map([...rawProjectsById].map(([id,record])=>[id,record.value]));
  const orchestratorRecords=[...rawOrchestratorsById.values()];
  const orchestratorsById=new Map(orchestratorRecords.map(record=>[record.id,record.value]));
  const tickets: NavigationTicket[]=[...rawTicketsById.values()].flatMap(record=>{
    if(record.kind!=='ticket') return [];
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    const owner=row.ceiling_holder.kind==='owner' && row.ceiling_holder.id==='owner';
    const parked=row.ticket_status==='awaiting_approval';
    const parent=row.orchestrator_kind==='chief' ? null : row.sprint_item_id;
    return [{id:record.id,created_at:row.created_at,terminal:row.terminal,title:row.title,stage:row.stage,worker_type:row.worker_type,
      ticket_status:row.ticket_status,is_backlogged:row.is_backlogged,blocked:row.blocked,
      activity_at:row.updated_at,conversation_id:row.conversation_id,conversation_signal:signal(knowledge),
      awaiting_reply:value(knowledge?.message_for_owner),awaiting_answer:value(knowledge?.awaiting_answer),
      awaiting_approval:parked && owner,awaiting_agent_approval:parked && !owner,assigned:row.stage_ownership==='user',
      agent_state:agentState(row.ticket_status,row.stage,knowledge),
      waiting_to_closeout:row.gating_field==='consequences' && row.ticket_status==='empty',
      gating_field:row.gating_field,recap:row.recap,recap_preview:row.recap_preview,
      orchestrator:parent === null ? {kind:'chief',id:'chief'} : {kind:'sprint_item',id:parent},
      sprint_item_id:parent,sprint_item_title:parent === null ? null : orchestratorsById.get(parent)?.title ?? null}];
  });
  const ticketsByParent=new Map<string,NavigationTicket[]>();
  const ticketMastheadsById=new Map<string,NavigationTicketMasthead>();
  for(const ticket of tickets) {
    const key=ticket.orchestrator?.kind==='sprint_item' ? `orchestrator:${ticket.orchestrator.id}` : 'chief:chief';
    const siblings=ticketsByParent.get(key) ?? [];
    siblings.push(ticket);ticketsByParent.set(key,siblings);
    const raw=rawTicketsById.get(ticket.id)!.value;
    const parent=raw.sprint_item_id===null ? undefined : rawOrchestratorsById.get(raw.sprint_item_id);
    if(raw.sprint_item_id!==null && !parent) continue;
    const projectId=raw.sprint_item_id===null ? raw.project_id : parent!.value.project_id;
    if(projectId!==null && !rawProjectsById.has(projectId)) continue;
    ticketMastheadsById.set(ticket.id,{id:ticket.id,title:raw.title,worker_type:raw.worker_type,
      project:projectId===null ? null : rawProjectsById.get(projectId)!.value.name,
      conversation_id:raw.conversation_id,conversation_signal:signal(raw.conversation_id===null ? undefined : view.conversations.get(raw.conversation_id))});
  }
  const orchestrators: WorkspaceOrchestrator[]=orchestratorRecords.map(record=>{
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    return {id:record.id,title:row.title,created_at:row.created_at,
      settled:row.settled,project_id:row.project_id,project:projects.get(row.project_id)?.name,
      recap:row.recap,recap_preview:row.recap_preview,conversation_id:row.conversation_id,
      conversation_signal:signal(knowledge),tickets:ticketsByParent.get(`orchestrator:${record.id}`) ?? [],
      awaiting_reply:value(knowledge?.message_for_owner),awaiting_answer:value(knowledge?.awaiting_answer),
      awaiting_approval:false,awaiting_agent_approval:false,assigned:false,agent_state:agentState(null,null,knowledge)};
  });
  const chief=chiefRecord?.kind==='chief' ? {
    label:chiefRecord.value.label,conversation_id:chiefRecord.value.conversation_id,
    conversation_signal:signal(chiefRecord.value.conversation_id === null ? undefined : view.conversations.get(chiefRecord.value.conversation_id)),
    awaiting_reply:chiefRecord.value.conversation_id === null ? null : value(view.conversations.get(chiefRecord.value.conversation_id)?.message_for_owner),
  } : null;
  const byTicket=new Map(tickets.map(ticket=>[ticket.id,ticket]));
  const messages: WorkspaceMessageConversation[]=[];
  const seen=new Set<string>();
  // Preserve exact address membership before deduplication. Current addresses win within an owner.
  const addresses=[...addressesByConversation.values()].flat().sort((a,b)=>Number(b.value.current)-Number(a.value.current) || a.id.localeCompare(b.id));
  for(const record of addresses) {
    const address=record.value;
    const knowledge=view.conversations.get(address.conversation_id);
    if(value(knowledge?.message_for_owner)!==true) continue;
    const key=`${address.owner_kind}:${address.owner_id}:${address.conversation_id}`;
    if(seen.has(key)) continue;
    const ticket=byTicket.get(address.owner_id), item=orchestratorsById.get(address.owner_id);
    const parentId=address.parent_kind==='chief' ? 'chief' : address.parent_id;
    const parent=parentId === null ? undefined : orchestratorsById.get(parentId);
    const title=address.owner_kind==='ticket' ? ticket?.title : address.owner_kind==='orchestrator' ? item?.title : chief?.label;
    if(title===undefined) continue;
    seen.add(key);
    messages.push({kind:address.owner_kind==='orchestrator' ? 'sprint_item' : address.owner_kind,
      id:address.owner_id,title,conversationId:address.conversation_id,conversationSignal:signal(knowledge),
      parentId,parentTitle:parentId==='chief' ? chief?.label ?? null : parent?.title ?? null,
      settled:address.owner_kind==='orchestrator' ? item?.settled === true : parent?.settled === true});
  }
  const messagesByParent=new Map<string,WorkspaceMessageConversation[]>();
  for(const message of messages) {
    if(message.parentId===null || message.parentId===undefined) continue;
    const siblings=messagesByParent.get(message.parentId) ?? [];
    siblings.push(message);messagesByParent.set(message.parentId,siblings);
  }
  return {tickets,orchestrators,chief,messages,conversationSignals,rawTicketsById,rawOrchestratorsById,rawProjectsById,
    ticketsById:new Map(tickets.map(ticket=>[ticket.id,ticket])),orchestratorsById:new Map(orchestrators.map(item=>[item.id,item])),
    ticketsByParent,messagesByParent,ticketMastheadsById,addressesByConversation,
    review:finishReviewPresentation(view,reviewItems,currentConversationIds,exactOutClaimCount)};
}

/** Review follows the canonical complete queue, independently of sidebar filters. */
export function navigationReviewPresentation(view: EffectiveNavigationView): NavigationReviewPresentation {
  return navigationWorkspacePresentation(view).review;
}
type NavigationReviewPresentation = {
  items: import('../types').NavigationReviewItem[];
  runningConversationCount: number | null;
  exactOutClaimCount: number;
  currentConversationIds:ReadonlySet<string>;
};
function finishReviewPresentation(view:EffectiveNavigationView,items:import('../types').NavigationReviewItem[],pointers:Set<string>,exactOutClaimCount:number):NavigationReviewPresentation {
  items.sort((a,b) => a.waiting_since-b.waiting_since || (a.ticket_id < b.ticket_id ? -1 : a.ticket_id > b.ticket_id ? 1 : 0));
  let runningConversationCount: number | null = view.runtimeCurrent ? 0 : null;
  for (const id of pointers) {
    const fact = view.conversations.get(id)?.working;
    if (!fact || fact.state !== 'fresh') { runningConversationCount = null; break; }
    if (runningConversationCount !== null && fact.value) runningConversationCount += 1;
  }
  return {items,runningConversationCount,exactOutClaimCount,currentConversationIds:pointers};
}

export function reviewProposalIdentity(item: import('../types').NavigationReviewItem): string {
  return JSON.stringify([item.ticket_id,item.field,item.proposal_field,item.waiting_since,item.proposal_revision]);
}

/** Summary identity admits the body; detail and manifest cannot rewrite the queue. */
export function reviewProposalDetailMatches(item: import('../types').NavigationReviewItem, detail: Pick<import('../types').TicketDetail,'id' | 'pending_proposal'>): boolean {
  const proposal = detail.pending_proposal;
  return detail.id === item.ticket_id && proposal !== null && proposal.field === item.proposal_field
    && proposal.created_at === item.waiting_since && proposal.revision === item.proposal_revision;
}

/** Retained metadata never retains authority for a conversation fact. */
export function navigationConversationSignal(view:EffectiveNavigationView | null | undefined,conversationId:string | null):Partial<ConversationSignal> {
  return signal(conversationId===null ? undefined : view?.conversations.get(conversationId));
}

/** Masthead facts never become an actionable TicketDetail. */
export type NavigationTicketMasthead = {
  readonly id:string;
  readonly title:string;
  readonly worker_type:string;
  readonly project:string | null;
  readonly conversation_id:string | null;
  readonly conversation_signal:Partial<ConversationSignal>;
};
export function navigationTicketMasthead(view:EffectiveNavigationView,id:string):NavigationTicketMasthead | undefined {
  return navigationWorkspacePresentation(view).ticketMastheadsById.get(id);
}

export type NavigationPresentationIndexes = {
  readonly addressesByConversation:ReadonlyMap<string,readonly AddressRecord[]>;
  readonly rawTicketsById:ReadonlyMap<string,TicketRecord>;
  readonly rawOrchestratorsById:ReadonlyMap<string,OrchestratorRecord>;
  readonly rawProjectsById:ReadonlyMap<string,ProjectRecord>;
  readonly ticketsById:ReadonlyMap<string,NavigationTicket>;
  readonly orchestratorsById:ReadonlyMap<string,WorkspaceOrchestrator>;
  readonly ticketsByParent:ReadonlyMap<string,readonly NavigationTicket[]>;
  readonly messagesByParent:ReadonlyMap<string,readonly WorkspaceMessageConversation[]>;
  readonly ticketMastheadsById:ReadonlyMap<string,NavigationTicketMasthead>;
};
export type NavigationPresentation = NavigationWorkspacePresentation & NavigationPresentationIndexes & {
  readonly review:NavigationReviewPresentation;
};

/** Provider lifetime owns this cache; a knowledge-only change has a different key. */
export function createNavigationPresentationCache() {
  const cache=new WeakMap<EffectiveNavigationView,NavigationPresentation>();
  return (view:EffectiveNavigationView):NavigationPresentation => {
    let result=cache.get(view);
    if(!result) {result=navigationWorkspacePresentation(view);cache.set(view,result);}
    return result;
  };
}

// Held section metadata reuses the same per-fact presentation rules.
export { agentState as navigationAgentState, value as navigationKnownBoolean };
