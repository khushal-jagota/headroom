import { fetchJson } from "../api";
import { SEND_DEADLINE_MILLISECONDS } from "./outgoing";
import type { DeliveredMessage, OwnerSendBody } from "./wire";

export type SendOwner = { kind: "chief" | "manager" | "ticket"; id: string };

export function ownerSendPath(owner: SendOwner): string {
  if (owner.kind === "chief") return "/api/chief/conversation/send";
  const category = owner.kind === "manager" ? "managers" : "tickets";
  return `/api/${category}/${encodeURIComponent(owner.id)}/conversation/send`;
}

/** The response ends the send. The conversation tail and application change stream
 * reconcile reads independently. Attachment uploads retain their longer deadline. */
export function postOwnerSend(path: string, body: OwnerSendBody): Promise<DeliveredMessage> {
  const hasAttachment = body.content.some(piece => piece.piece === "image" || piece.piece === "file");
  return fetchJson<DeliveredMessage>(path, {
    method: "POST",
    body,
    signal: AbortSignal.timeout(hasAttachment ? SEND_DEADLINE_MILLISECONDS : 30_000)
  });
}

export async function readOwnerSend(
  owner: SendOwner,
  messageId: string,
  signal?: AbortSignal
): Promise<DeliveredMessage | null> {
  const query = new URLSearchParams({ owner_kind: owner.kind, owner_id: owner.id });
  const deadline = AbortSignal.timeout(25_000);
  const { delivery } = await fetchJson<{ delivery: DeliveredMessage | null }>(
    `/api/conversation/owner-sends/${encodeURIComponent(messageId)}?${query}`,
    { cache: "no-store", signal: signal ? AbortSignal.any([signal, deadline]) : deadline }
  );
  return delivery;
}
