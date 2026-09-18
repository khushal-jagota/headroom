import type { ConversationState } from "./conversationState";

/** Select the conversation state for a new Ticket visit. */
export function initialTicketConversationState(
  effectiveOwnership: string | null | undefined
): ConversationState {
  return effectiveOwnership === "user" ? "opened" : "rest";
}
