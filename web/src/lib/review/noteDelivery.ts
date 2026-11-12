import { mintOutgoingMessage, type OutgoingMessage } from "../conversation/outgoing";
import { fateSentence } from "../conversation/composer";
import type { DeliveredMessage } from "../conversation/wire";

/** A receipt belongs to one exact proposal, including its revision. Keep it until
 * the decision succeeds: the accept request can fail after the note arrives. */
export type ReviewNoteDelivery = {
  conversationId: string | null;
  message: OutgoingMessage;
  status: "pending" | "delivered";
};

function storageKey(identity: string): string {
  return `panels:review-note:${identity}`;
}

function storageError(): Error {
  return new Error("The approval note cannot be recovered safely. Browser storage must be available before approval.");
}

export function reviewNoteText(delivery: ReviewNoteDelivery): string {
  const piece = delivery.message.content[0];
  return piece.piece === "text" ? piece.text : "";
}

export function recallReviewNote(identity: string): ReviewNoteDelivery | null {
  try {
    const stored = window.sessionStorage.getItem(storageKey(identity));
    if (stored === null) return null;
    const value = JSON.parse(stored) as ReviewNoteDelivery;
    const message = value?.message;
    if ((value.conversationId !== null && typeof value.conversationId !== "string")
      || (value.status !== "pending" && value.status !== "delivered")
      || !message || typeof message.messageId !== "string" || !message.messageId
      || !Number.isFinite(message.sentAtUnixMilliseconds)
      || message.senderLabel !== "owner" || message.mode !== "queue"
      || !Array.isArray(message.content) || message.content.length !== 1
      || message.content[0]?.piece !== "text" || typeof message.content[0].text !== "string") {
      throw storageError();
    }
    return value;
  } catch {
    throw storageError();
  }
}

function rememberReviewNote(identity: string, delivery: ReviewNoteDelivery | null): void {
  try {
    if (delivery) window.sessionStorage.setItem(storageKey(identity), JSON.stringify(delivery));
    else window.sessionStorage.removeItem(storageKey(identity));
  } catch {
    throw storageError();
  }
}

export function forgetReviewNote(identity: string): void {
  // The proposal is resolved. An obsolete receipt cannot affect another revision.
  try { window.sessionStorage.removeItem(storageKey(identity)); } catch { /* best effort */ }
}

/** Resolve the original message first, even if the reviewer changed or cleared the
 * draft. Retrying its identity asks the conversation for that message's own fate. */
export async function deliverReviewNote(options: {
  identity: string;
  conversationId: string | null;
  text: string;
  send: (delivery: ReviewNoteDelivery) => Promise<DeliveredMessage>;
  onChange: (delivery: ReviewNoteDelivery | null) => void;
}): Promise<void> {
  let delivery = recallReviewNote(options.identity);
  async function confirm(original: ReviewNoteDelivery, recovering: boolean): Promise<void> {
    // Persist before the request, so a reload during the request retains its name.
    rememberReviewNote(options.identity, original);
    options.onChange(original);
    let result: DeliveredMessage;
    try {
      result = await options.send(original);
    } catch (error) {
      const status = (error as { status?: number }).status;
      // A first request rejected before admission can be corrected and sent anew.
      // A rejection on recovery says nothing about the earlier ambiguous request.
      if (!recovering && status !== undefined && status >= 400 && status < 500) {
        rememberReviewNote(options.identity, null);
        options.onChange(null);
      }
      throw error;
    }
    if (original.conversationId === null && typeof result.conversation_id === "string") {
      original = { ...original, conversationId: result.conversation_id };
      rememberReviewNote(options.identity, original);
      options.onChange(original);
    }
    if (result.fate === "started" || result.fate === "queued" || result.fate === "injected") {
      delivery = { ...original, status: "delivered" };
      rememberReviewNote(options.identity, delivery);
      options.onChange(delivery);
      return;
    }
    if (result.fate === "refused" || result.fate === "suppressed") {
      rememberReviewNote(options.identity, null);
      options.onChange(null);
    }
    throw Object.assign(new Error(fateSentence(result) ?? result.fate), { code: "not_approved" });
  }
  if (delivery?.status === "pending") await confirm(delivery, true);
  if (options.text && (!delivery || reviewNoteText(delivery) !== options.text)) {
    await confirm({
      conversationId: options.conversationId,
      message: mintOutgoingMessage({
        content: [{ piece: "text", text: options.text }], senderLabel: "owner", mode: "queue"
      }),
      status: "pending"
    }, false);
  }
}
