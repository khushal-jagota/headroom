import { type Lifecycle } from "./lifecycle";

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
