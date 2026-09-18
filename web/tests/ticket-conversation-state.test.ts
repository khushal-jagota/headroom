import { describe, expect, it } from "vitest";

import { initialTicketConversationState } from "../src/lib/conversation/ticketConversationState";

describe("Ticket conversation state", () => {
  it("opens a user-owned Ticket conversation", () => {
    expect(initialTicketConversationState("user")).toBe("opened");
  });

  it.each(["empty", "agent", "awaiting_approval", "needs_user", "blocked", "done"])(
    "opens a %s Ticket at rest",
    (ticketStatus) => {
      expect(initialTicketConversationState(ticketStatus)).toBe("rest");
    }
  );
});
