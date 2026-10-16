import { stageLabelFor, type Lifecycle } from "./lifecycle";

export type PendingCeilingIntent = "scope" | "approve";

/** A scope edit grants approval only when it raises both stored permission and the
 * waiting boundary. Explicit Approve can approve work under already-higher permission. */
export function pendingCeilingApproves(
  lifecycle: Lifecycle | null, pendingStage: string, savedCeiling: string | null,
  selectedCeiling: string | null, ceilingTouched: boolean, intent: PendingCeilingIntent
): boolean {
  if (!lifecycle || !selectedCeiling) return false;
  const selected = lifecycle.stageOrder.indexOf(selectedCeiling);
  const waiting = lifecycle.stageOrder.indexOf(pendingStage);
  if (selected < 0 || waiting < 0 || selected <= waiting) return false;
  return intent === "approve" || (ceilingTouched && selected > lifecycle.stageOrder.indexOf(savedCeiling ?? ""));
}

export function pendingCeilingEffect(
  lifecycle: Lifecycle | null, pendingStage: string, pendingLabel: string,
  savedCeiling: string | null, selectedCeiling: string | null, ceilingTouched: boolean,
  intent: PendingCeilingIntent, reviewerChanged: boolean, reviewerLabel: string
): { approves: boolean; text: string } {
  const approves = pendingCeilingApproves(lifecycle,pendingStage,savedCeiling,selectedCeiling,ceilingTouched,intent);
  if (approves) return { approves, text:`Approves this ${pendingLabel} proposal and carries on to ${stageLabelFor(lifecycle,selectedCeiling ?? "")}.` };
  if (reviewerChanged) return { approves, text:`Sends this ${pendingLabel} proposal to ${reviewerLabel}. Nothing is approved.` };
  return { approves, text:`Nothing is approved while the ceiling stays at ${stageLabelFor(lifecycle,selectedCeiling ?? pendingStage)}.` };
}
