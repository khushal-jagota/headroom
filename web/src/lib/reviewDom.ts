import { reviewProposalIdentity, reviewProposalDetailMatches } from './navigation/presentation';
import { renderMarkdownToElement, serializeMarkdownDomToSource } from './markdownPipeline';
export function reviewDomMatches(root:HTMLElement,item:import('./types').NavigationReviewItem | null,count:number | null):boolean {
  if (item) return root.querySelector('[data-review-card]')?.getAttribute('data-proposal-identity')===reviewProposalIdentity(item);
  const empty=root.querySelector('[data-review-empty]');
  const meta=empty?.querySelector('.review-empty-meta');
  return Boolean(empty) && (count === null ? !meta : meta?.textContent===`${count} ${count===1 ? 'agent' : 'agents'} in progress`);
}

/** Check actual editor DOM for the admitted body, not a summary marker alone. */
export function reviewProposalDomMatches(root:HTMLElement,item:import('./types').NavigationReviewItem,detail:import('./types').TicketDetail):boolean {
  if (!reviewProposalDetailMatches(item,detail) || detail.pending_proposal?.field!==item.field
    || root.getAttribute('data-proposal-identity')!==reviewProposalIdentity(item)) return false;
  if (root.querySelector('.review-ticket-title [contenteditable]')?.textContent !== item.title) return false;
  const editor=root.querySelector<HTMLElement>('[data-approval-block] [data-edit]');
  return Boolean(editor && detail.pending_proposal
    && serializeMarkdownDomToSource(editor)===serializeMarkdownDomToSource(renderMarkdownToElement(detail.pending_proposal.body)));
}

