import { actualMark, expectedMark } from './conversationSignalDom';
import type { OrchestratorPrincipal, ListPageFacts } from './types';
import { workItemTicketOrder, type WorkItemTicketFacts } from './workItemPresentation';
import { workspaceAddress } from './workspaceAddress';

/** Verify only painted section facts. Closed history attests its heading and count. */
export function childrenSectionDomMatches(
  root: HTMLElement, principal: OrchestratorPrincipal, filter: string,
  rows: readonly WorkItemTicketFacts[],
  groups: readonly {key:string; label:string; count?:number; tickets:readonly WorkItemTicketFacts[]}[],
  page: ListPageFacts | undefined, open: Readonly<Record<string,boolean>>,
  sectionOpen: boolean, count: number, scopedWindow?:import("./navigation/contracts").ViewWindow, scrolling=false,
): boolean {
  if (root.dataset.workerSection !== filter) return false;
  const details = [...root.querySelectorAll<HTMLDetailsElement>('details[data-workspace-group]')];
  function ticketRowsMatch(element: Element, tickets: readonly WorkItemTicketFacts[]): boolean {
    const nodes = [...element.querySelectorAll<HTMLElement>('[data-sprint-ticket-id]')];
    return nodes.length === tickets.length && tickets.every((ticket,index) => {
      const node = nodes[index];
      const address = principal.kind === 'sprint_item'
        ? {kind:'ticket' as const,id:ticket.id,openedFromItemId:principal.id}
        : {kind:'ticket' as const,id:ticket.id};
      return node.dataset.sprintTicketId === ticket.id
        && node.querySelector('.ticket-row-title')?.textContent === ticket.title
        && node.getAttribute('href') === workspaceAddress(address)
        && JSON.stringify(actualMark(node)) === JSON.stringify(expectedMark(ticket.conversation_signal,ticket.conversation_id));
    });
  }
  if (filter === 'active') {
    if (details.length !== groups.length || !groups.every((group,index) => {
      const node = details[index];
      return node.dataset.workspaceGroup === group.key && node.open === (open[group.key] ?? true)
        && node.querySelector('.sprint-workspace-status-label')?.textContent === group.label
        && node.querySelector('[data-group-count]')?.textContent === String(group.count ?? group.tickets.length)
        && (!node.open || ticketRowsMatch(node,group.tickets));
    })) return false;
  } else {
    const node = details[0];
    if (details.length !== 1 || node.dataset.workspaceGroup !== (filter === 'backlogged' ? 'backlog' : 'done')
      || node.open !== sectionOpen
      || node.querySelector('.sprint-workspace-status-label')?.textContent !== (filter === 'backlogged' ? 'Backlogged' : 'Done')
      || node.querySelector('[data-group-count]')?.textContent !== String(count)) return false;
    if (!node.open) return true;
    if (!ticketRowsMatch(node,[...rows].sort(workItemTicketOrder))) return false;
  }
  const empty = root.querySelector('.sprint-workspace-empty');
  const expectedEmpty = filter === 'active' ? 'No active Workers' : filter === 'backlogged' ? 'No backlogged Workers' : 'No completed Workers';
  if ((filter==='active' && scrolling ? count===0 : rows.length===0) ? empty?.textContent !== expectedEmpty : empty !== null) return false;
  const controls = root.querySelector('.child-pages');
  if(scopedWindow && scrolling)return controls===null;
  if(scopedWindow){
    if(!scopedWindow.previous && !scopedWindow.next)return controls===null;
    const buttons=controls?.querySelectorAll('button');
    return controls?.querySelector('span')?.textContent===`${scopedWindow.members.length} of ${scopedWindow.total}`
      && buttons?.[0].disabled===!scopedWindow.previous && buttons?.[1].disabled===!scopedWindow.next;
  }
  if (!page || !(page.omitted_before > 0 || page.omitted_after > 0)) return controls === null;
  const buttons = controls?.querySelectorAll('button');
  return controls?.querySelector('span')?.textContent === `${page.return_count ? page.omitted_before+1 : 0}–${page.omitted_before+page.return_count} of ${page.match_count}`
    && buttons?.[0].disabled === (page.omitted_before === 0)
    && buttons?.[1].disabled === (page.next_offset === null);
}
