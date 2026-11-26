import { fetchJson } from "../api";
import { mutateJson } from "../mutate";
import { sendRequestLimitMs } from "./outgoing";
import { PROMPT_DISCARDED_SENTENCE } from "./transcript";
import type { DeliveredMessage, OwnerSendBody, OwnerSendOutcome, PromptDeliveryRefusalReason } from "./wire";

export type SendOwner = { kind: "chief" | "manager" | "ticket"; id: string };

export function ownerSendPath(owner: SendOwner): string {
  if (owner.kind === "chief") return "/api/chief/conversation/send";
  const category = owner.kind === "manager" ? "managers" : "tickets";
  return `/api/${category}/${encodeURIComponent(owner.id)}/conversation/send`;
}

/** The response ends the send. The conversation tail and application change stream
 * reconcile reads independently. Attachment uploads retain their longer deadline. */
export function postOwnerSend(path: string, body: OwnerSendBody): Promise<DeliveredMessage> {
  return mutateJson<DeliveredMessage>(path, {
    method: "POST",
    body,
    limitMs: sendRequestLimitMs(body.content)
  });
}

/** What home knows about one owner message, or null when it does not know the ID. */
export async function readOwnerSend(
  owner: SendOwner,
  messageId: string,
  signal?: AbortSignal
): Promise<OwnerSendOutcome | null> {
  const query = new URLSearchParams({ owner_kind: owner.kind, owner_id: owner.id });
  const { outcome } = await fetchJson<{ outcome: OwnerSendOutcome | null }>(
    `/api/conversation/owner-sends/${encodeURIComponent(messageId)}?${query}`,
    { cache: "no-store", signal }
  );
  return outcome;
}

/** An outcome said in the send answer's own words, so one branch handles each case.
 *
 * Pending is still accepted. Delivered is a prompt the conversation recorded, which the
 * callers handle as a started send. Discarded is a message dropped on purpose before
 * delivery, which they handle as a suppressed send.
 */
export function ownerSendOutcomeAsDelivery(outcome: OwnerSendOutcome): DeliveredMessage {
  const conversation_id = outcome.conversation_id;
  switch (outcome.outcome) {
    case "pending": return { fate: "accepted", message_id: outcome.message_id, conversation_id };
    case "delivered": return { fate: "started", conversation_id };
    // The contract always names the reason. A missing one reads as the general sentence.
    case "refused": return { fate: "refused", refusal_reason: outcome.refusal_reason as PromptDeliveryRefusalReason, reason: outcome.reason, conversation_id };
    case "discarded": return { fate: "suppressed", reason: outcome.reason ?? PROMPT_DISCARDED_SENTENCE, conversation_id };
    case "uncertain": return { fate: "uncertain", conversation_id };
  }
}
