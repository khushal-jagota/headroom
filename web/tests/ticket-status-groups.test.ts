import { describe, expect, it } from "vitest";

import {
  TICKET_STATUS_GROUPS,
  ticketStatusGroupKey,
  type TicketStatusGroupFacts
} from "../src/lib/ticketStatusGroups";

function ticket(values: Partial<TicketStatusGroupFacts> = {}): TicketStatusGroupFacts {
  return {
    stage: "needs_implementation",
    ticket_status: "empty",
    has_pending_proposal: false,
    waiting_to_closeout: false,
    gating_field: "implementation",
    ...values
  };
}

describe("Ticket status groups", () => {
  it("holds one order, and names the groups that arrive open", () => {
    // Errored leads and Blocked sits late, as they do in the rail.
    expect(TICKET_STATUS_GROUPS.map((group) => group.label)).toEqual([
      "Errored",
      "Needs you",
      "User",
      "Waiting for kickoff",
      "Awaiting approval",
      "Paired",
      "Agent",
      "Waiting for closeout",
      "Empty",
      "Blocked",
      "Done"
    ]);
    expect(
      TICKET_STATUS_GROUPS.filter((group) => !group.quiet).map((group) => group.label)
    ).toEqual([
      "Errored",
      "Needs you",
      "User",
      "Waiting for kickoff",
      "Awaiting approval",
      "Paired"
    ]);
  });

  it("names each Ticket's group from the shared condition", () => {
    expect(ticketStatusGroupKey(ticket({ ticket_status: "needs_user" }))).toBe("needs-me");
    // Waiting on the user and being the user's own to do are two groups, as in the rail.
    expect(ticketStatusGroupKey(ticket({ ticket_status: "user" }))).toBe("user");
    expect(ticketStatusGroupKey(ticket({ ticket_status: "awaiting_approval" }))).toBe(
      "current-awaiting-approval"
    );
    expect(ticketStatusGroupKey(ticket({ has_pending_proposal: true }))).toBe(
      "current-awaiting-approval"
    );
    expect(ticketStatusGroupKey(ticket({ ticket_status: "paired" }))).toBe("current-paired");
    expect(ticketStatusGroupKey(ticket({ ticket_status: "agent" }))).toBe("current-running");
    // Errored and blocked are two states, and each names its own group.
    expect(ticketStatusGroupKey(ticket({ ticket_status: "errored" }))).toBe("errored");
    expect(ticketStatusGroupKey(ticket({ ticket_status: "blocked" }))).toBe("blocked");
    expect(ticketStatusGroupKey(ticket())).toBe("upcoming");
    expect(ticketStatusGroupKey(ticket({ stage: "done" }))).toBe("completed");
    expect(ticketStatusGroupKey(ticket({ waiting_to_closeout: true }))).toBe("current-waiting");
  });

  it("names a kickoff-gated Ticket by its gating field, whichever way it is parked", () => {
    // A parked proposal and a pending one reach the same shared awaiting-approval
    // condition, so the kickoff split reads the gating field.
    expect(
      ticketStatusGroupKey(ticket({ ticket_status: "awaiting_approval", gating_field: "kickoff" }))
    ).toBe("waiting-for-kickoff");
    expect(
      ticketStatusGroupKey(ticket({ has_pending_proposal: true, gating_field: "kickoff" }))
    ).toBe("waiting-for-kickoff");
    // The gating field only splits Tickets that are awaiting approval.
    expect(
      ticketStatusGroupKey(ticket({ ticket_status: "agent", gating_field: "kickoff" }))
    ).toBe("current-running");
  });

  it("reads a group from facts a screen may not carry", () => {
    // A row that knows nothing about gating still lands somewhere sane.
    expect(ticketStatusGroupKey({ stage: "needs_plan", ticket_status: "agent" })).toBe(
      "current-running"
    );
    expect(ticketStatusGroupKey({ stage: "needs_plan", ticket_status: "awaiting_approval" })).toBe(
      "current-awaiting-approval"
    );
  });
});
