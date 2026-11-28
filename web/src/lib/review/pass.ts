/** The other pass, as a button for the top band of each card: from the new items to the skipped ones, and back. */
export const REVIEW_PASS_CONTEXT = Symbol('review-pass');
export type ReviewPassAction = { label: string; run: () => void; disabled: boolean };
export type ReviewPassContext = { action: () => ReviewPassAction | null };
