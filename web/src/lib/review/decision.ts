import type { Principal } from "../types";

/** The body of the existing Ticket decision actions. The server owns attribution. */
export type ReviewDecisionBody = {
  decision_id: string;
  expected_proposal_revision: number;
  message: string | null;
  edited_body?: string;
  next_ceiling?: string;
  next_holder?: Principal;
};
export type ReviewDecision = { action: "approve" | "send_back"; body: ReviewDecisionBody };

export function decisionIdentity(ticketId: string, revision: number): string {
  return JSON.stringify([ticketId, revision]);
}
export function decisionId(identity: string, action: ReviewDecision["action"]): string {
  return `${identity}:${action}`;
}
function storageKey(identity: string): string { return `panels:review-decision:${identity}`; }
function storageError(): Error {
  return new Error("The previous decision cannot be recovered safely. Browser storage must be available before a decision.");
}

export function recallReviewDecision(identity: string): ReviewDecision | null {
  try {
    const raw = window.sessionStorage.getItem(storageKey(identity));
    if (raw === null) return null;
    const value = JSON.parse(raw) as ReviewDecision;
    if (!value || !["approve", "send_back"].includes(value.action)
      || !value.body || value.body.decision_id !== decisionId(identity, value.action)
      || !Number.isInteger(value.body.expected_proposal_revision)
      || (value.body.message !== null && typeof value.body.message !== "string")) throw storageError();
    return value;
  } catch { throw storageError(); }
}

/** Persist before the request. An ambiguous response keeps the exact decision for
 * reload and route-switch retries; no note travels separately from that decision. */
export async function submitReviewDecision(
  identity: string,
  decision: ReviewDecision,
  send: (body: ReviewDecisionBody) => Promise<unknown>
): Promise<unknown> {
  const previous = recallReviewDecision(identity);
  if (previous && JSON.stringify(previous) !== JSON.stringify(decision)) {
    throw new Error("The previous decision has no confirmed response. Retry its original note and approval choice before changing it.");
  }
  const captured = previous ?? decision;
  try { window.sessionStorage.setItem(storageKey(identity), JSON.stringify(captured)); }
  catch { throw storageError(); }
  let result: unknown;
  try { result = await send(captured.body); }
  catch (error) {
    const status = (error as { status?: number }).status;
    if (!previous && status !== undefined && status >= 400 && status < 500) forgetReviewDecision(identity);
    throw error;
  }
  forgetReviewDecision(identity);
  return result;
}
function forgetReviewDecision(identity: string): void {
  try { window.sessionStorage.removeItem(storageKey(identity)); } catch { /* The server confirmed this decision. */ }
}
