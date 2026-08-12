import { describe, expect, it } from "vitest";

import { initialTicketConversationState } from "../src/lib/conversation/ticketConversationState";

describe("Ticket conversation state", () => {
  it("opens a paired Ticket conversation", () => {
    expect(initialTicketConversationState("paired")).toBe("opened");
  });

  it.each(["empty", "agent", "awaiting_agent_review", "awaiting_user_review", "needs_user", "blocked", "done"])(
    "opens a %s Ticket at rest",
    (ticketStatus) => {
      expect(initialTicketConversationState(ticketStatus)).toBe("rest");
    }
  );
});
