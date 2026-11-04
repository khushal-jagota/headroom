import type { NavigationReviewItem, ReviewHistoryKey } from '../navigation/contracts';

/** Only the unfinished pass belongs to the visit. Fresh navigation data owns eligibility. */
export type ReviewVisit = {
  artifactHref: string;
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
