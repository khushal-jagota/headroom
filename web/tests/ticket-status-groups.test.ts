import { describe, expect, it } from "vitest";

import {
  TICKET_STATUS_GROUPS,
  ticketStatusGroupKey,
  type TicketStatusGroupFacts
} from "../src/lib/ticketStatusGroups";
import { workspaceGroups } from "../src/lib/workspaceRail";
import { boardCard } from "./boardCardFixture";

function ticket(values: Partial<TicketStatusGroupFacts> = {}): TicketStatusGroupFacts {
  return {
    stage: "needs_implementation",
    ticket_status: "empty",
    waiting_to_closeout: false,
    gating_field: "implementation",
    ...values
  };
}

// What a screen actually hands over: the payload still carries `has_pending_proposal`,
// and the grouping ignores it.
function ticketWithFiledProposal(values: Partial<TicketStatusGroupFacts> = {}) {
  return { ...ticket(values), has_pending_proposal: true };
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
    // The groups that arrive open here are the groups the rail holds. Agent is one of
    // them: work running right now is what the reader came for.
    expect(
      TICKET_STATUS_GROUPS.filter((group) => !group.quiet).map((group) => group.label)
    ).toEqual([
      "Errored",
      "Needs you",
      "User",
      "Waiting for kickoff",
      "Awaiting approval",
      "Paired",
      "Agent"
    ]);
  });

  it("names each Ticket's group from the shared condition", () => {
    expect(ticketStatusGroupKey(ticket({ ticket_status: "needs_user" }))).toBe("needs-me");
    // Waiting on the user and being the user's own to do are two groups, as in the rail.
    expect(ticketStatusGroupKey(ticket({ ticket_status: "user" }))).toBe("user");
    expect(ticketStatusGroupKey(ticket({ ticket_status: "awaiting_approval" }))).toBe(
      "current-awaiting-approval"
    );
    expect(ticketStatusGroupKey(ticket({ ticket_status: "paired" }))).toBe("current-paired");
    // Messaging a Ticket that was awaiting approval pairs it and leaves the proposal
    // filed. The Ticket is paired, and the filed proposal does not say otherwise.
    expect(ticketStatusGroupKey(ticketWithFiledProposal({ ticket_status: "paired" }))).toBe(
      "current-paired"
    );
    expect(ticketStatusGroupKey(ticket({ ticket_status: "agent" }))).toBe("current-running");
    // Errored and blocked are two states, and each names its own group.
    expect(ticketStatusGroupKey(ticket({ ticket_status: "errored" }))).toBe("errored");
    expect(ticketStatusGroupKey(ticket({ ticket_status: "blocked" }))).toBe("blocked");
    expect(ticketStatusGroupKey(ticket())).toBe("upcoming");
    expect(ticketStatusGroupKey(ticket({ stage: "done" }))).toBe("completed");
    expect(ticketStatusGroupKey(ticket({ waiting_to_closeout: true }))).toBe("current-waiting");
  });

  it("names a kickoff-gated Ticket by its gating field, and only while it awaits approval", () => {
    expect(
      ticketStatusGroupKey(ticket({ ticket_status: "awaiting_approval", gating_field: "kickoff" }))
    ).toBe("waiting-for-kickoff");
    // The gating field only splits Tickets that are awaiting approval.
    expect(
      ticketStatusGroupKey(ticket({ ticket_status: "agent", gating_field: "kickoff" }))
    ).toBe("current-running");
    // A kickoff proposal stays filed once the user messages the Ticket. It is paired.
    expect(
      ticketStatusGroupKey(
        ticketWithFiledProposal({ ticket_status: "paired", gating_field: "kickoff" })
      )
    ).toBe("current-paired");
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

  it("names a paired Ticket with a filed proposal the same on the rail and the Sprint Item page", () => {
    // The reproduction: the user messages a Ticket that was awaiting approval, and the
    // proposal stays filed. The two screens hold their own keys, so the label is where
    // they have to agree.
    const railGroups = workspaceGroups([
      boardCard("t_paired", { ticket_status: "paired", has_pending_proposal: true })
    ]);
    const pageKey = ticketStatusGroupKey(
      ticketWithFiledProposal({ ticket_status: "paired" })
    );
    const pageLabel = TICKET_STATUS_GROUPS.find((group) => group.key === pageKey)?.label;
    expect(railGroups.map((group) => group.label)).toEqual([pageLabel]);
    expect(pageLabel).toBe("Paired");
  });
});
