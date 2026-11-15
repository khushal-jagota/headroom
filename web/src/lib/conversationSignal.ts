/** Independent facts from one exact conversation, supplied by the server. */
export type ConversationSignal = {
  working: boolean;
  message_for_owner: boolean;
};

/** Canonical display precedence for the two independent facts. */
export function conversationSignalKind(signal:Partial<ConversationSignal> | null | undefined):'message' | 'working' | null {
  return signal?.message_for_owner ? 'message' : signal?.working ? 'working' : null;
}
