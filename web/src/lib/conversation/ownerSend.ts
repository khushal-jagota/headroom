import { fetchJson } from "../api";
import { mutateJson } from "../mutate";
import { sendRequestLimitMs } from "./outgoing";
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
  return mutateJson<DeliveredMessage>(path, {
    method: "POST",
    body,
    limitMs: sendRequestLimitMs(body.content)
  });
}

export async function readOwnerSend(
  owner: SendOwner,
  messageId: string,
  signal?: AbortSignal
): Promise<DeliveredMessage | null> {
  const query = new URLSearchParams({ owner_kind: owner.kind, owner_id: owner.id });
  const { delivery } = await fetchJson<{ delivery: DeliveredMessage | null }>(
    `/api/conversation/owner-sends/${encodeURIComponent(messageId)}?${query}`,
    { cache: "no-store", signal }
  );
  return delivery;
}
