import type { ReviewMessageItem } from '../types';
import { workspaceAddress } from '../workspaceAddress';
import type { ConversationEvent } from '../conversation/wire';
import { mintOutgoingMessage, rememberOutgoingMessages, recallOutgoingMessages, type OutgoingMessage } from '../conversation/outgoing';

export type OwnerMessage = Extract<ConversationEvent, {kind: 'message_to_owner'}>;
export function unreadOwnerMessages(events: readonly ConversationEvent[], after: number): OwnerMessage[] {
  return events.filter((event): event is OwnerMessage => event.kind === 'message_to_owner' && event.sequence > after);
}
export function reviewMessageHref(item: ReviewMessageItem): string {
  const owner = item.owner_kind === 'chief' ? {kind:'chief' as const} : {kind:item.owner_kind,id:item.owner_id};
  return workspaceAddress(owner,undefined,undefined,item.conversation_id);
}
// Use the existing durable outgoing store for files. These private keys never become
// transcript sends; they survive a card unmount, Skip, and a page reload.
export const draftStorageKey = (id:string) => `review-draft:${id}`;
export const attemptStorageKey = (id:string) => `review-reply:${id}`;
export async function readReviewDraft(id:string) { return (await recallOutgoingMessages(draftStorageKey(id)))[0]?.content ?? []; }
export async function saveReviewDraft(id:string, content:OutgoingMessage['content']):Promise<boolean> {
  return rememberOutgoingMessages(draftStorageKey(id),content.length ? [mintOutgoingMessage({content,senderLabel:'owner',mode:'steer'})] : []);
}
