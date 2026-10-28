import type { TicketValue, TicketRecord, ManagerRecord, ProjectRecord, AddressRecord, ViewWindow, ViewCounts, ViewContentReference, ViewSettledManagerRecord } from './contracts';
import type { EffectiveNavigationView, KnownFact, ConversationKnowledge } from './knowledge';
import type { AgentState } from '../types';
import type { WorkspaceTicket, WorkspaceManager, WorkspaceSettledManager, WorkspaceChief } from '../workspaceRail';
import type { ConversationSignal } from '../conversationSignal';
import type { WorkspaceMessageConversation } from '../types';

export type NavigationTicket = WorkspaceTicket & Pick<TicketValue,'created_at' | 'terminal' | 'is_backlogged'>;
export type NavigationWorkspacePresentation = {
  readonly tickets: NavigationTicket[];
  readonly managers: WorkspaceManager[];
  readonly settledManagers:WorkspaceSettledManager[];
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
  const rawManagersById=new Map<string,ManagerRecord>();
  const rawSettledManagersById=new Map<string,ViewSettledManagerRecord>();
  const rawProjectsById=new Map<string,ProjectRecord>();
  const addressesByConversation=new Map<string,AddressRecord[]>();
  const reviewItems: import('../types').NavigationReviewItem[]=[];
  const reviewTickets=new Map<string,import('../types').NavigationReviewTicketItem>();
  const currentConversationIds=new Set<string>();
  let exactOutClaimCount=0;
  let chiefRecord:Extract<(typeof records)[number],{kind:'chief'}> | undefined;
  for(const record of records) {
    if(record.kind==='ticket' || record.kind==='manager' || record.kind==='chief') {
      if(record.value.conversation_id!==null) currentConversationIds.add(record.value.conversation_id);
    }
    if(record.kind==='ticket') {
      const row=record.value;
      if(row.worker_step_claim_out) exactOutClaimCount+=1;
      if(!row.is_backlogged && row.ticket_status==='awaiting_approval' && row.ceiling_holder.kind==='owner' && row.ceiling_holder.id==='owner'
        && row.gating_field!==null && row.proposal_field!==null && row.proposal_created_at!==null) {
        const proposal:import('../types').NavigationReviewProposalItem={review_item_type:'proposal',ticket_id:record.id,field:row.gating_field,
          title:row.title,waiting_since:row.proposal_created_at,proposal_field:row.proposal_field,proposal_revision:row.proposal_revision};
        reviewTickets.set(record.id,{review_item_type:'ticket',ticket_id:record.id,title:row.title,waiting_since:proposal.waiting_since,proposal,messages:[]});
      }
    }
    if(record.kind==='ticket') rawTicketsById.set(record.id,record);
    else if(record.kind==='manager') {
      if('recap' in record.value)rawManagersById.set(record.id,{kind:record.kind,id:record.id,value:record.value});
      else rawSettledManagersById.set(record.id,{kind:record.kind,id:record.id,value:record.value});
    }
    else if(record.kind==='project') rawProjectsById.set(record.id,record);
    else if(record.kind==='chief') chiefRecord=record;
    else if(record.kind==='address') {const addresses=addressesByConversation.get(record.value.conversation_id) ?? [];addresses.push(record);addressesByConversation.set(record.value.conversation_id,addresses);}
  }
  const projects=new Map([...rawProjectsById].map(([id,record])=>[id,record.value]));
  const managerRecords=[...rawManagersById.values()];
  const managersById=new Map([...managerRecords,...rawSettledManagersById.values()].map(record=>[record.id,record.value]));
  const tickets: NavigationTicket[]=[...rawTicketsById.values()].flatMap(record=>{
    if(record.kind!=='ticket') return [];
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    const owner=row.ceiling_holder.kind==='owner' && row.ceiling_holder.id==='owner';
    const parked=row.ticket_status==='awaiting_approval';
    const parent=row.manager_kind==='chief' ? null : row.manager_id;
    return [{id:record.id,locale:row.locale === "macos" ? "macos" as const : "vps" as const,created_at:row.created_at,terminal:row.terminal,title:row.title,stage:row.stage,worker_type:row.worker_type,
      ticket_status:row.ticket_status,is_backlogged:row.is_backlogged,blocked:row.blocked,
      activity_at:row.updated_at,conversation_id:row.conversation_id,conversation_signal:signal(knowledge),
      awaiting_reply:value(knowledge?.message_for_owner),awaiting_answer:value(knowledge?.awaiting_answer),
      awaiting_approval:parked && owner,awaiting_agent_approval:parked && !owner,assigned:row.stage_ownership==='user',
      agent_state:agentState(row.ticket_status,row.stage,knowledge),
      waiting_to_closeout:row.gating_field==='consequences' && row.ticket_status==='empty',
      gating_field:row.gating_field,recap:row.recap,recap_reference:'recap_reference' in row ? row.recap_reference as ViewContentReference|null : null,recap_preview:row.recap_preview,
      manager:parent === null ? {kind:'chief',id:'chief'} : {kind:'manager',id:parent},
      manager_id:parent,manager_title:parent === null ? null : managersById.get(parent)?.title ?? null}];
  });
  const ticketsByParent=new Map<string,NavigationTicket[]>();
  const ticketMastheadsById=new Map<string,NavigationTicketMasthead>();
  for(const ticket of tickets) {
    const key=ticket.manager?.kind==='manager' ? `manager:${ticket.manager.id}` : 'chief:chief';
    const siblings=ticketsByParent.get(key) ?? [];
    siblings.push(ticket);ticketsByParent.set(key,siblings);
    const raw=rawTicketsById.get(ticket.id)!.value;
    const parent=raw.manager_id===null ? undefined : rawManagersById.get(raw.manager_id);
    if(raw.manager_id!==null && !parent) continue;
    const projectId=raw.manager_id===null ? raw.project_id : parent!.value.project_id;
    if(projectId!==null && !rawProjectsById.has(projectId)) continue;
    ticketMastheadsById.set(ticket.id,{id:ticket.id,locale:ticket.locale ?? "vps",title:raw.title,worker_type:raw.worker_type,
      project:projectId===null ? null : rawProjectsById.get(projectId)!.value.name,
      conversation_id:raw.conversation_id,conversation_signal:signal(raw.conversation_id===null ? undefined : view.conversations.get(raw.conversation_id))});
  }
  const managers: WorkspaceManager[]=managerRecords.map(record=>{
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    return {id:record.id,title:row.title,created_at:row.created_at,updated_at:row.updated_at,
      settled:row.settled,project_id:row.project_id,project:projects.get(row.project_id)?.name,
      recap:row.recap,recap_reference:'recap_reference' in row ? row.recap_reference as ViewContentReference|null : null,recap_preview:row.recap_preview,conversation_id:row.conversation_id,
      conversation_signal:signal(knowledge),tickets:ticketsByParent.get(`manager:${record.id}`) ?? [],
      awaiting_reply:value(knowledge?.message_for_owner),awaiting_answer:value(knowledge?.awaiting_answer),
      awaiting_approval:false,awaiting_agent_approval:false,assigned:false,agent_state:agentState(null,null,knowledge)};
  });
  const settledManagers:WorkspaceSettledManager[]=[...rawSettledManagersById.values()].map(record=>{
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    return {id:record.id,...row,conversation_signal:signal(knowledge),tickets:ticketsByParent.get(`manager:${record.id}`) ?? [],
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
  const addresses=[...addressesByConversation.values()].flat().sort((a,b)=>Number(b.value.current)-Number(a.value.current) || compareIdentity(a.id,b.id));
  for(const record of addresses) {
    const address=record.value;
    const knowledge=view.conversations.get(address.conversation_id);
    if(value(knowledge?.message_for_owner)!==true) continue;
    const key=`${address.owner_kind}:${address.owner_id}:${address.conversation_id}`;
    if(seen.has(key)) continue;
    const ticket=byTicket.get(address.owner_id), item=managersById.get(address.owner_id);
    const parentId=address.parent_kind==='chief' ? 'chief' : address.parent_id;
    const parent=parentId === null ? undefined : managersById.get(parentId);
    const title=address.owner_kind==='ticket' ? ticket?.title : address.owner_kind==='manager' ? item?.title : chief?.label;
    if(title===undefined) continue;
    seen.add(key);
    messages.push({kind:address.owner_kind==='manager' ? 'manager' : address.owner_kind,
      id:address.owner_id,title,conversationId:address.conversation_id,conversationSignal:signal(knowledge),
      parentId,parentTitle:parentId==='chief' ? chief?.label ?? null : parent?.title ?? null,
      settled:address.owner_kind==='manager' ? item?.settled === true : parent?.settled === true});
  }
  const conversationsById=new Map(records.flatMap(record=>record.kind==='conversation' ? [[record.id,record.value] as const] : []));
  const reviewConversations=new Set<string>();
  for(const record of addresses) {
    const address=record.value;
    if(reviewConversations.has(address.conversation_id))continue;
    if(value(view.conversations.get(address.conversation_id)?.message_for_owner)!==true)continue;
    const conversation=conversationsById.get(address.conversation_id);
    if(conversation?.first_unread_owner_message_at == null)continue;
    // Select the canonical address before the owner-state filter. Never fall back
    // to a former owner when the canonical owner is excluded.
    reviewConversations.add(address.conversation_id);
    const owner=address.owner_kind==='ticket' ? rawTicketsById.get(address.owner_id)?.value
      : address.owner_kind==='manager' ? managersById.get(address.owner_id) : chiefRecord?.value;
    if(!owner)continue;
    if(address.owner_kind==='ticket' && ('stage' in owner && owner.stage==='done' || 'is_backlogged' in owner && owner.is_backlogged))continue;
    if(address.owner_kind==='manager' && 'settled' in owner && owner.settled)continue;
    const message:import('../types').ReviewMessageItem={review_item_type:'message',conversation_id:address.conversation_id,address_id:record.id,
      owner_kind:address.owner_kind,owner_id:address.owner_id,title:'title' in owner ? owner.title : owner.label,
      waiting_since:conversation.first_unread_owner_message_at,recap:'recap' in owner ? owner.recap : '',
      recap_reference:'recap_reference' in owner ? owner.recap_reference as ViewContentReference|null : null};
    if(address.owner_kind==='ticket') {
      let card=reviewTickets.get(address.owner_id);
      if(!card) {
        card={review_item_type:'ticket',ticket_id:address.owner_id,title:message.title,waiting_since:message.waiting_since,proposal:null,messages:[]};
        reviewTickets.set(address.owner_id,card);
      }
      card.messages.push(message);
      // A card represents the oldest thing waiting on the owner. Proposal identity
      // retains its own timestamp even when an older message sets queue position.
      card.waiting_since=Math.min(card.waiting_since,message.waiting_since);
    } else reviewItems.push(message);
  }
  reviewItems.push(...reviewTickets.values());
  const addressesById=new Map([...addressesByConversation.values()].flat().map(record=>[record.id,record]));
  const unreadConversationIdsByWindow=new Map<string,ReadonlySet<string>>();
  for(const window of view.publication.view?.windows ?? []) {
    if(window.request.section!=='unread')continue;
    const ids=new Set<string>();
    for(const key of window.members) {
      const address=key.kind==='address' ? addressesById.get(key.id) : undefined;
      if(address)ids.add(address.value.conversation_id);
    }
    unreadConversationIdsByWindow.set(window.window_id,ids);
  }
  return {tickets,managers,settledManagers,chief,messages,conversationSignals,rawTicketsById,rawManagersById,rawProjectsById,
    ticketsById:byTicket,managersById:new Map(managers.map(item=>[item.id,item])),
    ticketsByParent,ticketMastheadsById,addressesByConversation,
    unreadConversationIdsByWindow,
    review:finishReviewPresentation(view,reviewItems,currentConversationIds,exactOutClaimCount),
    viewWindows:view.publication.view?.windows ?? [],globalCounts:view.publication.view?.counts ?? null};
}

function compareIdentity(a:string,b:string):number { return a<b ? -1 : a>b ? 1 : 0; }

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
  items.sort((a,b) => a.waiting_since-b.waiting_since
    || compareIdentity(a.review_item_type,b.review_item_type)
    || compareIdentity(a.review_item_type==='message' ? a.conversation_id : a.ticket_id,
      b.review_item_type==='message' ? b.conversation_id : b.ticket_id));
  const counts=view.publication.view?.counts;
  if(counts)exactOutClaimCount=counts.out_claims;
  return {items,runningConversationCount:navigationWorkingAgentCount(view),exactOutClaimCount,currentConversationIds:pointers};
}

/** Workspace-wide count. A scoped view supplies its exact total without a resident-row scan. */
export function navigationWorkingAgentCount(view:EffectiveNavigationView | null | undefined):number | null {
  if(!view?.runtimeCurrent)return null;
  const counts=view.publication.view?.counts;
  if(counts)return counts.running_conversations;
  const pointers=new Set<string>();
  for(const record of view.publication.records) {
    if((record.kind==='ticket' || record.kind==='manager' || record.kind==='chief') && record.value.conversation_id!==null) {
      pointers.add(record.value.conversation_id);
    }
  }
  let count=0;
  for(const id of pointers) {
    const fact=view.conversations.get(id)?.working;
    if(!fact || fact.state!=='fresh')return null;
    if(fact.value)count+=1;
  }
  return count;
}

export function reviewProposalIdentity(item: import('../types').NavigationReviewProposalItem): string {
  return JSON.stringify([item.ticket_id,item.field,item.proposal_field,item.waiting_since,item.proposal_revision]);
}

/** Summary identity admits the body; detail and manifest cannot rewrite the queue. */
export function reviewProposalDetailMatches(item: import('../types').NavigationReviewProposalItem, detail: Pick<import('../types').TicketDetail,'id' | 'pending_proposal'>): boolean {
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
  readonly locale:import("../ticketMachines").TicketLocale;
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
  readonly unreadConversationIdsByWindow:ReadonlyMap<string,ReadonlySet<string>>;
  readonly addressesByConversation:ReadonlyMap<string,readonly AddressRecord[]>;
  readonly rawTicketsById:ReadonlyMap<string,TicketRecord>;
  readonly rawManagersById:ReadonlyMap<string,ManagerRecord>;
  readonly rawProjectsById:ReadonlyMap<string,ProjectRecord>;
  readonly ticketsById:ReadonlyMap<string,NavigationTicket>;
  readonly managersById:ReadonlyMap<string,WorkspaceManager>;
  readonly ticketsByParent:ReadonlyMap<string,readonly NavigationTicket[]>;
  readonly ticketMastheadsById:ReadonlyMap<string,NavigationTicketMasthead>;
};
export type NavigationPresentation = NavigationWorkspacePresentation & NavigationPresentationIndexes & {
  readonly review:NavigationReviewPresentation;
  readonly viewWindows:readonly ViewWindow[];
  readonly globalCounts:ViewCounts | null;
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

/** Membership comes from the server window, not dependency rows resident for another surface. */
export function navigationWindow(view:NavigationPresentation|null|undefined,section:ViewWindow['request']['section'],owner:ViewWindow['request']['owner']=null,childFilter:ViewWindow['request']['child_filter']='all',group:ViewWindow['request']['group']='all'):ViewWindow|undefined {
  return view?.viewWindows.find(window=>window.request.section===section && window.request.owner?.kind===owner?.kind && window.request.owner?.id===owner?.id && window.request.child_filter===childFilter && window.request.group===group);
}
