/** Independent facts from one exact conversation, supplied by the server. `setting_up` is the
 *  one Ticket fact beside them: its Worker is being prepared, often before any conversation. */
export type ConversationSignal = {
  working: boolean;
  message_for_owner: boolean;
  setting_up?: boolean;
};

/** Canonical display precedence for the independent facts. */
export function conversationSignalKind(signal:Partial<ConversationSignal> | null | undefined):'message' | 'working' | 'setting_up' | null {
  return signal?.message_for_owner ? 'message' : signal?.working ? 'working' : signal?.setting_up ? 'setting_up' : null;
}
