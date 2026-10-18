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
