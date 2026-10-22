import { type WorkspaceRail, type WorkspaceChief, type WorkspaceTicketGroup } from './workspaceRail';
import type { WorkItemTicketFacts } from './workItemPresentation';
import type { ConversationSignal } from './conversationSignal';
import { actualMark, expectedMark } from './conversationSignalDom';
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
export function railDomMatches(root:HTMLElement,rail:WorkspaceRail,chief:WorkspaceChief | null | undefined,settledOpen:boolean,settledCount=rail.settledItems.length):boolean {
  const cards=[...root.querySelectorAll<HTMLElement>('.active-work-scroll > .manager-card')];
  if(cards.length!==rail.items.length+1) return false;
  const head=(card:HTMLElement,title:string,signal:Partial<ConversationSignal> | undefined,id:string | null | undefined)=>
    card.querySelector('.item-head .item-title')?.textContent===title
      && JSON.stringify(actualMark(card.querySelector('.item-head')!))===JSON.stringify(expectedMark(signal,id));
  if(!head(cards[0],chief?.label ?? 'Chief of Staff',chief?.conversation_signal,chief?.conversation_id)
    || !groupsMatch(cards[0],rail.chiefGroups)) return false;
  if(!rail.items.every((item,index)=>cards[index+1].dataset.manager===item.id
    && cards[index+1].querySelector('.manager-eyebrow > span')?.textContent===(item.project ?? '')
    && cards[index+1].querySelector('.settle')?.getAttribute('aria-label')===`Settle ${item.title}`
    && head(cards[index+1],item.title,item.conversationSignal,item.conversationId) && groupsMatch(cards[index+1],item.groups))) return false;
  if(root.querySelector('[data-settled-count]')?.textContent!==`(${settledCount})`) return false;
  if(root.querySelector('.settled [data-message-conversation]')) return false;
  const settled=[...root.querySelectorAll<HTMLElement>('[data-settled-item]')];
  return settledOpen ? settled.length===rail.settledItems.length && rail.settledItems.every((item,index)=>
    settled[index].dataset.settledItem===item.id && settled[index].querySelector('.settled-title > span')?.textContent===item.title
      && JSON.stringify(actualMark(settled[index].querySelector('.settled-title')!))===JSON.stringify(expectedMark(item.conversationSignal,item.conversationId))
      && settled[index].querySelector('.settled-title')?.getAttribute('aria-label')===`Open ${item.title}`
      && settled[index].querySelector('.reopen')?.getAttribute('aria-label')===`Reopen ${item.title}`) : settled.length===0;
}

export function hoverDomMatches(root:HTMLElement,title:string,recap:string,stage:string,signal:Partial<ConversationSignal> | undefined,
  conversationId:string | null | undefined,kicker:string):boolean {
  return root.querySelector('.kicker')?.textContent===kicker && root.getAttribute('aria-hidden')==='false' && root.querySelector('.title > span')?.textContent===title
    && (root.querySelector('[data-recap-text]')?.textContent ?? '')===recap && (root.querySelector('.doing')?.textContent ?? '')===stage
    && JSON.stringify(actualMark(root))===JSON.stringify(expectedMark(signal,conversationId));
}

