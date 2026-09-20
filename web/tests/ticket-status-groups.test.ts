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
    awaiting_reply: false,
    awaiting_approval: false,
    assigned: false,
    agent_state: "idle",
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
      "Waiting for kickoff",
      "Awaiting approval",
      "Assigned",
      "Agent",
      "Awaiting an agent's approval",
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
      "Waiting for kickoff",
      "Awaiting approval",
      "Assigned",
      "Agent"
    ]);
  });

  it("names each Ticket's group from the shared condition", () => {
    expect(ticketStatusGroupKey(ticket({ awaiting_reply: true }))).toBe("needs-me");
    expect(ticketStatusGroupKey(ticket({ awaiting_approval: true }))).toBe(
      "current-awaiting-approval"
    );
    expect(ticketStatusGroupKey(ticket({ assigned: true }))).toBe("current-assigned");
    expect(ticketStatusGroupKey(ticketWithFiledProposal({ assigned: true }))).toBe(
      "current-assigned"
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
      ticketStatusGroupKey(ticket({ awaiting_approval: true, gating_field: "kickoff" }))
    ).toBe("waiting-for-kickoff");
    // The gating field only splits Tickets that are awaiting approval.
    expect(
      ticketStatusGroupKey(ticket({ ticket_status: "agent", gating_field: "kickoff" }))
    ).toBe("current-running");
    expect(
      ticketStatusGroupKey(
        ticketWithFiledProposal({ assigned: true, gating_field: "kickoff" })
      )
    ).toBe("current-assigned");
  });

  it("reads a group from facts a screen may not carry", () => {
    // A row that knows nothing about gating still lands somewhere sane.
    expect(ticketStatusGroupKey(ticket({ stage: "needs_plan", ticket_status: "agent" }))).toBe(
      "current-running"
    );
    expect(ticketStatusGroupKey(ticket({ stage: "needs_plan", awaiting_approval: true }))).toBe(
      "current-awaiting-approval"
    );
  });

  // Two screens, one answer. Both of these split the same Ticket two ways before this
  // Ticket: the rail read the dispatch status while the page read the live turn, and the
  // page read the failed turn while the rail read a status nothing writes.
  it("names a dispatched Ticket Agent on both screens, with no turn in process", () => {
    const facts = { ticket_status: "agent", agent_state: "idle" } as const;
    const railGroups = workspaceGroups([boardCard("t_dispatched", facts)]);
    const pageKey = ticketStatusGroupKey(ticket(facts));
    expect(railGroups.map((group) => group.label)).toEqual(["Agent"]);
    expect(TICKET_STATUS_GROUPS.find((group) => group.key === pageKey)?.label).toBe("Agent");
  });

  it("names a worker that broke Errored on both screens, from its failed last turn", () => {
    const facts = { ticket_status: "agent", agent_state: "errored" } as const;
    const railGroups = workspaceGroups([boardCard("t_failed", facts)]);
    const pageKey = ticketStatusGroupKey(ticket(facts));
    expect(railGroups.map((group) => group.label)).toEqual(["Errored"]);
    expect(TICKET_STATUS_GROUPS.find((group) => group.key === pageKey)?.label).toBe("Errored");
  });

  it("uses the Workspace's owner-facing Assigned label for an assigned Ticket", () => {
    const railGroups = workspaceGroups([
      boardCard("t_assigned", { assigned: true, has_pending_proposal: true })
    ]);
    const pageKey = ticketStatusGroupKey(
      ticketWithFiledProposal({ assigned: true })
    );
    const pageLabel = TICKET_STATUS_GROUPS.find((group) => group.key === pageKey)?.label;
    expect(railGroups.map((group) => group.label)).toEqual(["Assigned"]);
    expect(pageLabel).toBe("Assigned");
  });
});
