import type { NavigationReviewItem, NavigationReviewProposalItem, ReviewHistoryKey } from '../navigation/contracts';

/** Only the unfinished pass belongs to the visit. Fresh navigation data owns eligibility. */
export type ReviewVisit = {
  artifactHref: string;
  /** The proposal that was on screen when the artifact was opened, so the artifact page can
   *  show that proposal's decision area. Null when the card on screen was not a proposal. */
  proposal: NavigationReviewProposalItem | null;
  anchor: string | null;
  skipped: Record<string, boolean>;
  skippedHistory: ReviewHistoryKey[];
  returnCohort: ReviewHistoryKey[];
  currentKey: string | null;
  finalLatched: boolean;
  proposalDraft: { identity: string; text: string; editedBody?: string } | null;
};

// Scope memory to the application, rather than to a component or a browser reload.
const visits = new WeakMap<object, ReviewVisit>();
export function rememberReviewVisit(application: object, visit: ReviewVisit): void {
  visits.set(application, visit);
}
/** The visit while the artifact page is up. Review takes it back when it returns. */
export function peekReviewVisit(application: object): ReviewVisit | undefined {
  return visits.get(application);
}
export function takeReviewVisit(application: object): ReviewVisit | undefined {
  const visit = visits.get(application);
  visits.delete(application);
  return visit;
}
export function reviewReturnHref(application: object, artifactHref: string): string | null {
  return visits.get(application)?.artifactHref === artifactHref ? '#/review' : null;
}
export function reviewVisitItemKey(item: NavigationReviewItem): string {
  return item.review_item_type === 'message' ? `message:${item.conversation_id}` : `ticket:${item.ticket_id}`;
}
