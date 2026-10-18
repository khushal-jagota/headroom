import { reviewProposalIdentity, reviewProposalDetailMatches } from './presentation';
import { renderMarkdownToElement, serializeMarkdownDomToSource } from '../markdownPipeline';
import { workspaceAddress } from '../workspaceAddress';
import { workspaceRailChildMessageConversations } from '../workspaceRail';
import type { WorkspaceMessageConversation } from '../types';
import type { WorkspaceTicket, WorkspaceRail, WorkspaceChief, WorkspaceTicketGroup } from '../workspaceRail';
import type { WorkItemTicketFacts } from '../workItemPresentation';
import type { ConversationSignal } from '../conversationSignal';
const mark=(signal:Partial<ConversationSignal> | undefined)=>signal?.message_for_owner ? 'message' : signal?.working ? 'working' : null;
function actualMark(root: Element) {
  const element=root.querySelector('[data-conversation-signal]');
  return element ? [element.getAttribute('data-conversation-signal'),element.closest('[data-conversation-id]')?.getAttribute('data-conversation-id') ?? null] : [null,null];
}
function expectedMark(signal:Partial<ConversationSignal> | undefined,id:string | null | undefined) {
  const value=mark(signal); return [value,value ? id ?? null : null];
}
function groupsMatch(root: Element,groups: readonly WorkspaceTicketGroup[]) {
  const buckets=[...root.querySelectorAll(':scope > .bucket')];
  if(buckets.length!==groups.length) return false;
  return groups.every((group,index)=>{
    const bucket=buckets[index];
    const rows=[...bucket.querySelectorAll<HTMLElement>('[data-ticket-id]')];
    return bucket.getAttribute('data-bucket-key')===group.key && bucket.querySelector('.bucket-label')?.textContent===group.label
      && rows.length===group.cards.length && group.cards.every((ticket,row)=>ticketMatches(rows[row],ticket));
  });
}
function ticketMatches(row:HTMLElement,ticket:WorkItemTicketFacts) {
  return row.dataset.ticketId===ticket.id && row.dataset.ticketStage===ticket.stage
    && row.dataset.ticketStatus===ticket.ticket_status && row.querySelector('.ticket-row-title')?.textContent===ticket.title
    && JSON.stringify(actualMark(row))===JSON.stringify(expectedMark(ticket.conversation_signal,ticket.conversation_id));
}
/** Read actual rendered values, including motion-controlled membership and marks. */
export function railDomMatches(root:HTMLElement,rail:WorkspaceRail,chief:WorkspaceChief | null | undefined,settledOpen:boolean,messages:readonly WorkspaceMessageConversation[],tickets:readonly WorkspaceTicket[]):boolean {
  const cards=[...root.querySelectorAll<HTMLElement>('.active-work-scroll > .orchestrator-card')];
  if(cards.length!==rail.items.length+1) return false;
  const head=(card:HTMLElement,title:string,signal:Partial<ConversationSignal> | undefined,id:string | null | undefined)=>
    card.querySelector('.item-head .item-title')?.textContent===title
      && JSON.stringify(actualMark(card.querySelector('.item-head')!))===JSON.stringify(expectedMark(signal,id));
  if(!head(cards[0],chief?.label ?? 'Chief of Staff',chief?.conversation_signal,chief?.conversation_id)
    || !groupsMatch(cards[0],rail.chiefGroups)) return false;
  if(!rail.items.every((item,index)=>cards[index+1].dataset.sprintItem===item.id
    && cards[index+1].querySelector('.orchestrator-eyebrow > span')?.textContent===(item.project ?? '')
    && cards[index+1].querySelector('.settle')?.getAttribute('aria-label')===`Settle ${item.title}`
    && head(cards[index+1],item.title,item.conversationSignal,item.conversationId) && groupsMatch(cards[index+1],item.groups))) return false;
  if(root.querySelector('[data-settled-count]')?.textContent!==`(${rail.settledItems.length})`) return false;
  const settled=[...root.querySelectorAll<HTMLElement>('[data-settled-item]')];
  return settledOpen ? settled.length===rail.settledItems.length && rail.settledItems.every((item,index)=>
    settled[index].dataset.settledItem===item.id && settled[index].querySelector('.settled-title > span')?.textContent===item.title
      && JSON.stringify(actualMark(settled[index].querySelector('.settled-title')!))===JSON.stringify(expectedMark(item.conversationSignal,item.conversationId))
      && messagesMatch(settled[index],workspaceRailChildMessageConversations(messages,{...item,settled:item.settled},tickets))) : settled.length===0;
}

function messagesMatch(root:Element,expected:readonly WorkspaceMessageConversation[]):boolean {
  const rows=[...root.querySelectorAll<HTMLElement>('[data-message-conversation]')];
  return rows.length===expected.length && expected.every((row,index)=>rows[index].dataset.messageConversation===row.conversationId
    && rows[index].dataset.messageKind===row.kind && rows[index].querySelector('.ticket-row-title')?.textContent===row.title
    && JSON.stringify(actualMark(rows[index]))===JSON.stringify(expectedMark(row.conversationSignal,row.conversationId)));
}
export function hoverDomMatches(root:HTMLElement,title:string,recap:string,stage:string,signal:Partial<ConversationSignal> | undefined,
  conversationId:string | null | undefined,kicker:string):boolean {
  return root.querySelector('.kicker')?.textContent===kicker && root.getAttribute('aria-hidden')==='false' && root.querySelector('.title > span')?.textContent===title
    && (root.querySelector('.recap')?.textContent ?? '')===recap && (root.querySelector('.doing')?.textContent ?? '')===stage
    && JSON.stringify(actualMark(root))===JSON.stringify(expectedMark(signal,conversationId));
}

/** Only visible child rows attest paint; closed groups attest their label/count alone. */
export function childrenDomMatches(root:HTMLElement,principal:import('../types').OrchestratorPrincipal,filter:string,
  groups:readonly {key:string;label:string;tickets:readonly WorkItemTicketFacts[]}[],
  page:import('../types').ListPageFacts | undefined,open:Readonly<Record<string,boolean>>):boolean {
  if(root.dataset.orchestratorChildren!==`${principal.kind}:${principal.id}` || root.querySelector('select')?.value!==filter) return false;
  const actual=[...root.querySelectorAll<HTMLDetailsElement>('details[data-workspace-group]')];
  if(actual.length!==groups.length || !groups.every((group,index)=>{
    const node=actual[index];
    if(node.dataset.workspaceGroup!==group.key || node.open!==(open[group.key] ?? group.key!=='done')
      || node.querySelector('.sprint-workspace-status-label')?.textContent!==group.label
      || node.querySelector('[data-group-count]')?.textContent!==String(group.tickets.length)) return false;
    if(!node.open) return true;
    const rows=[...node.querySelectorAll<HTMLElement>('[data-sprint-ticket-id]')];
    return rows.length===group.tickets.length && group.tickets.every((ticket,i)=>rows[i].dataset.sprintTicketId===ticket.id
      && rows[i].querySelector('.ticket-row-title')?.textContent===ticket.title
      && rows[i].getAttribute('href')===workspaceAddress(principal.kind==='sprint_item' ? {kind:'ticket',id:ticket.id,openedFromItemId:principal.id} : {kind:'ticket',id:ticket.id})
      && JSON.stringify(actualMark(rows[i]))===JSON.stringify(expectedMark(ticket.conversation_signal,ticket.conversation_id)));
  })) return false;
  const controls=root.querySelector('.child-pages');
  if(!page || !(page.omitted_before>0 || page.omitted_after>0)) return controls===null;
  const buttons=controls?.querySelectorAll('button');
  return controls?.querySelector('span')?.textContent===`${page.omitted_before+1}–${page.omitted_before+page.return_count} of ${page.match_count}`
    && buttons?.[0].disabled===(page.omitted_before===0) && buttons?.[1].disabled===(page.next_offset===null);
}

export function workspaceHeaderDomMatches(root:HTMLElement,item:import('../workspaceRail').WorkspaceOrchestrator):boolean {
  return root.querySelector('.page-eyebrow > span')?.textContent===(item.project ?? '')
    && root.querySelector('h1 [contenteditable]')?.textContent===item.title
    && root.querySelector('.page-settle')?.getAttribute('aria-label')===`${item.settled ? 'Reopen' : 'Settle'} ${item.title}`
    && JSON.stringify(actualMark(root.querySelector('h1')!))===JSON.stringify(expectedMark(item.conversation_signal,item.conversation_id));
}

export function shellRuntimeDomMatches(root:HTMLElement,count:number | null,label:string):boolean {
  const presence=root.querySelector('[data-shell-presence]');
  return root.getAttribute('aria-label')===`${label}.${count === null ? '' : ` ${count} working.`}`
    && (count === null ? presence === null : presence?.textContent?.trim()===`${count} working`
      && Boolean(presence.querySelector('.shell-presence-spin'))===(count>0));
}

export function reviewDomMatches(root:HTMLElement,item:import('../types').NavigationReviewItem | null,count:number | null):boolean {
  if (item) return root.querySelector('[data-review-card]')?.getAttribute('data-proposal-identity')===reviewProposalIdentity(item);
  const empty=root.querySelector('[data-review-empty]');
  const meta=empty?.querySelector('.review-empty-meta');
  return Boolean(empty) && (count === null ? !meta : meta?.textContent===`${count} ${count===1 ? 'agent' : 'agents'} in progress`);
}

/** Check actual editor DOM for the admitted body, not a summary marker alone. */
export function reviewProposalDomMatches(root:HTMLElement,item:import('../types').NavigationReviewItem,detail:import('../types').TicketDetail):boolean {
  if (!reviewProposalDetailMatches(item,detail) || detail.pending_proposal?.field!==item.field
    || root.getAttribute('data-proposal-identity')!==reviewProposalIdentity(item)) return false;
  if (root.querySelector('.review-ticket-title [contenteditable]')?.textContent !== item.title) return false;
  if (item.field === 'brief' && root.querySelector<HTMLSelectElement>('[data-review-priority-control] select')?.value !== item.priority) return false;
  const editor=root.querySelector<HTMLElement>('[data-approval-block] [data-edit]');
  return Boolean(editor && detail.pending_proposal
    && serializeMarkdownDomToSource(editor)===serializeMarkdownDomToSource(renderMarkdownToElement(detail.pending_proposal.body)));
}

export function reviewBadgeDomMatches(root:HTMLElement,count:number):boolean {
  const badge=root.querySelector('.nav-badge');
  return count > 0 ? badge?.textContent===String(count) && !badge.classList.contains('hidden')
    : badge?.classList.contains('hidden')===true && badge.textContent==='';
}
