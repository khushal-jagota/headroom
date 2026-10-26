import { reviewProposalIdentity, reviewProposalDetailMatches } from './navigation/presentation';
import { renderMarkdownToElement, serializeMarkdownDomToSource } from './markdownPipeline';
export function reviewDomMatches(root:HTMLElement,item:import('./types').NavigationReviewItem | null,count:number | null):boolean {
  if (item) {
    const messageMatches=(message:import('./types').ReviewMessageItem)=>[...root.querySelectorAll('[data-review-card][data-review-item-type="message"][data-review-loaded="true"]')]
      .some(element=>element.getAttribute('data-message-identity')===`message:${message.conversation_id}`);
    if(item.review_item_type==='message')return messageMatches(item);
    const card=[...root.querySelectorAll('[data-review-ticket-card]')].find(element=>element.getAttribute('data-ticket-id')===item.ticket_id);
    if(!card || card.querySelector('.review-ticket-title [contenteditable]')?.textContent!==item.title)return false;
    const proposal=card.querySelector('[data-proposal-identity]');
    if(item.proposal ? proposal?.getAttribute('data-proposal-identity')!==reviewProposalIdentity(item.proposal) : Boolean(proposal))return false;
    return item.messages.every(messageMatches) && card.querySelectorAll('[data-review-item-type="message"]').length===item.messages.length;
  }
  const empty=root.querySelector('[data-review-empty]');
  const meta=empty?.querySelector('.review-empty-meta');
  return Boolean(empty) && (count === null ? !meta : meta?.textContent===`${count} ${count===1 ? 'agent' : 'agents'} in progress`);
}

/** Check actual editor DOM for the admitted body, not a summary marker alone. */
export function reviewProposalDomMatches(root:HTMLElement,item:import('./types').NavigationReviewProposalItem,detail:import('./types').TicketDetail):boolean {
  if (!reviewProposalDetailMatches(item,detail) || detail.pending_proposal?.field!==item.field
    || root.getAttribute('data-proposal-identity')!==reviewProposalIdentity(item)) return false;
  const heading=root.closest('[data-review-ticket-card]') ?? root;
  if (heading.querySelector('.review-ticket-title [contenteditable]')?.textContent !== item.title) return false;
  const editor=root.querySelector<HTMLElement>('[data-approval-block] [data-edit]');
  return Boolean(editor && detail.pending_proposal
    && serializeMarkdownDomToSource(editor)===serializeMarkdownDomToSource(renderMarkdownToElement(detail.pending_proposal.body)));
}

