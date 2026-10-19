import { workspaceAddress } from './workspaceAddress';
import type { WorkItemTicketFacts } from './workItemPresentation';
import { orchestratorGroupDefaultOpen } from './orchestratorChildren';
import { actualMark, expectedMark } from './conversationSignalDom';
/** Only visible child rows attest paint; closed groups attest their label/count alone. */
export function childrenDomMatches(root:HTMLElement,principal:import('./types').OrchestratorPrincipal,filter:string,
  groups:readonly {key:string;label:string;tickets:readonly WorkItemTicketFacts[]}[],
  page:import('./types').ListPageFacts | undefined,open:Readonly<Record<string,boolean>>):boolean {
  if(root.dataset.orchestratorChildren!==`${principal.kind}:${principal.id}` || root.querySelector('select')?.value!==filter) return false;
  const actual=[...root.querySelectorAll<HTMLDetailsElement>('details[data-workspace-group]')];
  if(actual.length!==groups.length || !groups.every((group,index)=>{
    const node=actual[index];
    if(node.dataset.workspaceGroup!==group.key || node.open!==(open[group.key] ?? orchestratorGroupDefaultOpen(group.key))
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

export function workspaceHeaderDomMatches(root:HTMLElement,item:import('./workspaceRail').WorkspaceOrchestrator):boolean {
  return root.querySelector('.page-eyebrow > span')?.textContent===(item.project ?? '')
    && root.querySelector('h1 [contenteditable]')?.textContent===item.title
    && root.querySelector('.page-settle')?.getAttribute('aria-label')===`${item.settled ? 'Reopen' : 'Settle'} ${item.title}`
    && JSON.stringify(actualMark(root.querySelector('h1')!))===JSON.stringify(expectedMark(item.conversation_signal,item.conversation_id));
}

