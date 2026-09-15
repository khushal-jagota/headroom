import type { ConversationState } from "./conversationState";

export type OwnerReadSnapshot = {
  readonly latestSequence: number;
  readonly ownerReadThroughSequence: number;
};

export type OwnerReadEligibility = {
  readonly conversationState: ConversationState | null;
  readonly documentIsVisible: boolean;
  readonly windowIsFocused: boolean;
  readonly transcriptLatestSequence: number;
  readonly snapshot: OwnerReadSnapshot;
};

/** Return the transcript position that the owner can truthfully mark as read.
 *
 * A null conversation state is the full-container form of the pane. Layered panes count
 * as open once they are peeked or opened. The snapshot proves that the conversation
 * exists and supplies the durable read position, but only rows drawn in the transcript
 * supply a new position to acknowledge.
 */
export function eligibleOwnerReadSequence({
  conversationState,
  documentIsVisible,
  windowIsFocused,
  transcriptLatestSequence,
  snapshot
}: OwnerReadEligibility): number | null {
  if (conversationState === "rest") return null;
  if (!documentIsVisible || !windowIsFocused) return null;
  if (transcriptLatestSequence <= snapshot.ownerReadThroughSequence) return null;
  return transcriptLatestSequence;
}
