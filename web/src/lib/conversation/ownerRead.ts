import type { ConversationEvent } from "./wire";
import type { ConversationState } from "./conversationState";

export type OwnerReadSnapshot = {
  readonly latestSequence: number;
  readonly ownerReadThroughSequence: number;
};

export type OwnerReadEligibility = {
  readonly conversationState: ConversationState | null;
  readonly documentIsVisible: boolean;
  readonly windowIsFocused: boolean;
  readonly deliveredLatestSequence: number;
  readonly snapshot: OwnerReadSnapshot;
};

export type OwnerReadAttention = {
  readonly documentIsVisible: boolean;
  readonly windowIsFocused: boolean;
};

type AttentionListener = () => void;

export type OwnerReadDocumentAttentionTarget = {
  readonly visibilityState: string;
  hasFocus(): boolean;
  addEventListener(type: "visibilitychange", listener: AttentionListener): void;
  removeEventListener(type: "visibilitychange", listener: AttentionListener): void;
};

export type OwnerReadWindowAttentionTarget = {
  addEventListener(type: "focus" | "blur", listener: AttentionListener): void;
  removeEventListener(type: "focus" | "blur", listener: AttentionListener): void;
};

/** Return the transcript position that the owner can truthfully mark as read.
 *
 * A null conversation state is the full-container form of the pane. Layered panes count
 * as open when opened. The snapshot proves that the conversation
 * exists and supplies the durable read position. Both lenses acknowledge every delivered
 * row because runtime rows can sit between owner-visible messages and system outcomes.
 */
export function eligibleOwnerReadSequence({
  conversationState,
  documentIsVisible,
  windowIsFocused,
  deliveredLatestSequence,
  snapshot
}: OwnerReadEligibility): number | null {
  if (conversationState === "rest") return null;
  if (!documentIsVisible || !windowIsFocused) return null;
  if (deliveredLatestSequence <= snapshot.ownerReadThroughSequence) return null;
  return deliveredLatestSequence;
}

/** Keep the browser attention inputs current for the owner-read rule. */
export function watchOwnerReadAttention(
  documentTarget: OwnerReadDocumentAttentionTarget,
  windowTarget: OwnerReadWindowAttentionTarget,
  publish: (attention: OwnerReadAttention) => void
): () => void {
  const publishCurrentAttention = () => {
    publish({
      documentIsVisible: documentTarget.visibilityState === "visible",
      windowIsFocused: documentTarget.hasFocus()
    });
  };
  publishCurrentAttention();
  documentTarget.addEventListener("visibilitychange", publishCurrentAttention);
  windowTarget.addEventListener("focus", publishCurrentAttention);
  windowTarget.addEventListener("blur", publishCurrentAttention);
  return () => {
    documentTarget.removeEventListener("visibilitychange", publishCurrentAttention);
    windowTarget.removeEventListener("focus", publishCurrentAttention);
    windowTarget.removeEventListener("blur", publishCurrentAttention);
  };
}

/** A confirmed read merits an immediate canonical refresh only when it covers an
 * owner message not covered by the prior read/refresh. Use raw rows, not a lens.
 * The backend keeps actual work and explicit owner-message/read facts authoritative.
 */
export function ownerReadCoversNewMessage(
  events: readonly ConversationEvent[],
  previouslyReadThrough: number,
  acceptedReadThrough: number
): boolean {
  return events.some((event) => event.kind === "message_to_owner"
    && event.sequence > previouslyReadThrough
    && event.sequence <= acceptedReadThrough);
}
