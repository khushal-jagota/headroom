import type { TicketValue } from './contracts';
import type { EffectiveNavigationView, KnownFact, ConversationKnowledge } from './knowledge';
import type { AgentState, Priority } from '../types';
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
function priority(raw: string): Priority {
  if (raw === 'P0' || raw === 'P1' || raw === 'P2' || raw === 'P3') return raw;
  throw new Error('Invalid navigation priority');
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
export function navigationWorkspacePresentation(view: EffectiveNavigationView): NavigationWorkspacePresentation {
  const records=view.publication.records;
  const conversationSignals=new Map([...view.conversations].map(([id,knowledge])=>[id,signal(knowledge)]));
  const projects=new Map(records.flatMap(record=>record.kind==='project' ? [[record.id,record.value] as const] : []));
  const orchestratorRecords=records.filter(record=>record.kind==='orchestrator');
  const orchestratorsById=new Map(orchestratorRecords.map(record=>[record.id,record.value]));
  const tickets: NavigationTicket[]=records.flatMap(record=>{
    if(record.kind!=='ticket') return [];
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    const owner=row.ceiling_holder.kind==='owner' && row.ceiling_holder.id==='owner';
    const parked=row.ticket_status==='awaiting_approval';
    const parent=row.orchestrator_kind==='chief' ? null : row.sprint_item_id;
    return [{id:record.id,created_at:row.created_at,terminal:row.terminal,title:row.title,priority:priority(row.priority),stage:row.stage,worker_type:row.worker_type,
      ticket_status:row.ticket_status,is_backlogged:row.is_backlogged,blocked:row.blocked,
      activity_at:row.updated_at,conversation_id:row.conversation_id,conversation_signal:signal(knowledge),
      awaiting_reply:value(knowledge?.message_for_owner),awaiting_answer:value(knowledge?.awaiting_answer),
      awaiting_approval:parked && owner,awaiting_agent_approval:parked && !owner,assigned:row.stage_ownership==='user',
      agent_state:agentState(row.ticket_status,row.stage,knowledge),
      waiting_to_closeout:row.gating_field==='consequences' && row.ticket_status==='empty',
      gating_field:row.gating_field,recap_preview:row.recap_preview,
      orchestrator:parent === null ? {kind:'chief',id:'chief'} : {kind:'sprint_item',id:parent},
      sprint_item_id:parent,sprint_item_title:parent === null ? null : orchestratorsById.get(parent)?.title ?? null}];
  });
  const orchestrators: WorkspaceOrchestrator[]=orchestratorRecords.map(record=>{
    const row=record.value;
    const knowledge=row.conversation_id === null ? undefined : view.conversations.get(row.conversation_id);
    return {id:record.id,title:row.title,priority:priority(row.priority),created_at:row.created_at,
      settled:row.settled,project_id:row.project_id,project:projects.get(row.project_id)?.name,
      recap_preview:row.recap_preview,conversation_id:row.conversation_id,
      conversation_signal:signal(knowledge),tickets:tickets.filter(ticket=>ticket.orchestrator?.kind==='sprint_item' && ticket.orchestrator.id===record.id),
      awaiting_reply:value(knowledge?.message_for_owner),awaiting_answer:value(knowledge?.awaiting_answer),
      awaiting_approval:false,awaiting_agent_approval:false,assigned:false,agent_state:agentState(null,null,knowledge)};
  });
  const chiefRecord=records.find(record=>record.kind==='chief');
  const chief=chiefRecord?.kind==='chief' ? {
    label:chiefRecord.value.label,conversation_id:chiefRecord.value.conversation_id,
    conversation_signal:signal(chiefRecord.value.conversation_id === null ? undefined : view.conversations.get(chiefRecord.value.conversation_id)),
    awaiting_reply:chiefRecord.value.conversation_id === null ? null : value(view.conversations.get(chiefRecord.value.conversation_id)?.message_for_owner),
  } : null;
  const byTicket=new Map(tickets.map(ticket=>[ticket.id,ticket]));
  const messages: WorkspaceMessageConversation[]=[];
  const seen=new Set<string>();
  // Preserve exact address membership before deduplication. Current addresses win within an owner.
  const addresses=records.filter(record=>record.kind==='address').sort((a,b)=>Number(b.value.current)-Number(a.value.current) || a.id.localeCompare(b.id));
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
  return {tickets,orchestrators,chief,messages,conversationSignals};
}

/** Review follows the canonical complete queue, independently of sidebar filters. */
export function navigationReviewPresentation(view: EffectiveNavigationView): {
  items: import('../types').NavigationReviewItem[];
  runningConversationCount: number | null;
  exactOutClaimCount: number;
} {
  const pointers = new Set<string>();
  const items: import('../types').NavigationReviewItem[] = [];
  let exactOutClaimCount = 0;
  for (const record of view.publication.records) {
    if (record.kind === 'ticket') {
      const row = record.value;
      if (row.worker_step_claim_out) exactOutClaimCount += 1;
      if (row.conversation_id !== null) pointers.add(row.conversation_id);
      if (row.ticket_status === 'awaiting_approval' && row.ceiling_holder.kind === 'owner'
        && row.ceiling_holder.id === 'owner' && row.gating_field !== null && row.proposal_field !== null
        && row.proposal_created_at !== null) {
        items.push({priority:priority(row.priority),review_item_type:'proposal', ticket_id:record.id,field:row.gating_field,title:row.title,
          waiting_since:row.proposal_created_at,proposal_field:row.proposal_field,proposal_revision:row.proposal_revision});
      }
    } else if (record.kind === 'chief' || record.kind === 'orchestrator') {
      if (record.value.conversation_id !== null) pointers.add(record.value.conversation_id);
    }
  }
  items.sort((a,b) => a.waiting_since-b.waiting_since || (a.ticket_id < b.ticket_id ? -1 : a.ticket_id > b.ticket_id ? 1 : 0));
  let runningConversationCount: number | null = view.runtimeCurrent ? 0 : null;
  for (const id of pointers) {
    const fact = view.conversations.get(id)?.working;
    if (!fact || fact.state !== 'fresh') { runningConversationCount = null; break; }
    if (runningConversationCount !== null && fact.value) runningConversationCount += 1;
  }
  return {items,runningConversationCount,exactOutClaimCount};
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
