import type { ConversationState } from "./conversationState";

/** Select the conversation state for a new Ticket visit. */
export function initialTicketConversationState(ticketStatus: string): ConversationState {
  return ticketStatus === "paired" ? "opened" : "rest";
}
