import type { ConversationSignal } from "./conversationSignal";

/** A recap that is absent while its agent works is one on its way.
 *
 * Asking for a recap clears the line it replaces, so the gap plus a working agent is the
 * whole of what "it is coming" means. Both facts are already on every screen that shows a
 * recap, so no surface stores a pending flag and a reload shows the same thing.
 *
 * ``undefined`` is a recap this caller cannot see, which is not the same as an absent one.
 * A screen that does not hold the line cannot say anything about it, so it says nothing.
 */
export function recapPending(
  recap: string | null | undefined,
  signal: Partial<ConversationSignal> | null | undefined
): boolean {
  if (recap === undefined) return false;
  return !(recap ?? "").trim() && Boolean(signal?.working);
}
