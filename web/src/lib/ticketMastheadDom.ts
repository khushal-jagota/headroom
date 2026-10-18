import { actualMark, expectedMark } from './conversationSignalDom';
export function ticketMastheadDomMatches(root:HTMLElement,header:import('./navigation/presentation').NavigationTicketMasthead,typeLabel:string):boolean {
  return root.dataset.ticketMastheadId===header.id
    && root.querySelector('[data-project-fact]')?.textContent===(header.project || 'No project')
    && root.querySelector('[data-ticket-type]')?.textContent===typeLabel
    && root.querySelector('.ticket-title [contenteditable]')?.textContent===header.title
    && JSON.stringify(actualMark(root.querySelector('.ticket-title')!))===JSON.stringify(expectedMark(header.conversation_signal,header.conversation_id));
}
