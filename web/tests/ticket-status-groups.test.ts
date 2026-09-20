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
    awaiting_agent_approval: false,
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
    // The three that are the reader's own lead, then Errored, as they do in the rail.
    // Blocked sits late on both.
    expect(TICKET_STATUS_GROUPS.map((group) => group.label)).toEqual([
      "Needs your approval",
      "Yours",
      "Messages",
      "Errored",
      "Awaiting kickoff",
      "Agent",
      "Awaiting an agent's approval",
      "Waiting on Consequences",
      "Empty",
      "Blocked",
      "Done"
    ]);
    // The groups that arrive open here are the groups the rail holds. Agent is one of
    // them: work running right now is what the reader came for.
    expect(
      TICKET_STATUS_GROUPS.filter((group) => !group.quiet).map((group) => group.label)
    ).toEqual([
      "Needs your approval",
      "Yours",
      "Messages",
      "Errored",
      "Awaiting kickoff",
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
      ticketStatusGroupKey(ticket({ awaiting_approval: true, gating_field: "brief" }))
    ).toBe("waiting-for-kickoff");
    // The gating field only splits Tickets that are awaiting approval.
    expect(
      ticketStatusGroupKey(ticket({ ticket_status: "agent", gating_field: "brief" }))
    ).toBe("current-running");
    expect(
      ticketStatusGroupKey(
        ticketWithFiledProposal({ assigned: true, gating_field: "brief" })
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

  it("names a proposal held by an agent the same on both screens", () => {
    // An agent holds this ceiling, and the server says so. This Ticket used to fall into
    // Empty on the page while the rail named it.
    const facts = {
      ticket_status: "awaiting_approval",
      awaiting_agent_approval: true
    } as const;
    const railGroups = workspaceGroups([boardCard("t_agent_held", facts)]);
    const pageKey = ticketStatusGroupKey(ticketWithFiledProposal(facts));
    expect(railGroups.map((group) => group.label)).toEqual(["Awaiting an agent's approval"]);
    expect(TICKET_STATUS_GROUPS.find((group) => group.key === pageKey)?.label).toBe(
      "Awaiting an agent's approval"
    );
  });

  it("lets a broken worker and the user's own work outrank a proposal held by an agent", () => {
    // Both facts hold at once: a worker filed for its supervisor and then its last turn
    // failed. Errored leads on both screens, and a parked proposal never renames it.
    const brokenFacts = {
      ticket_status: "awaiting_approval",
      awaiting_agent_approval: true,
      agent_state: "errored"
    } as const;
    expect(
      workspaceGroups([boardCard("t_broken", brokenFacts)]).map((group) => group.label)
    ).toEqual(["Errored"]);
    expect(
      TICKET_STATUS_GROUPS.find(
        (group) => group.key === ticketStatusGroupKey(ticketWithFiledProposal(brokenFacts))
      )?.label
    ).toBe("Errored");

    const mineFacts = {
      ticket_status: "awaiting_approval",
      awaiting_agent_approval: true,
      assigned: true
    } as const;
    expect(
      workspaceGroups([boardCard("t_mine", mineFacts)]).map((group) => group.label)
    ).toEqual(["Yours"]);
    expect(
      TICKET_STATUS_GROUPS.find(
        (group) => group.key === ticketStatusGroupKey(ticketWithFiledProposal(mineFacts))
      )?.label
    ).toBe("Yours");
  });

  it("names a stage that is the user's own Yours on both screens", () => {
    const railGroups = workspaceGroups([
      boardCard("t_assigned", { assigned: true, has_pending_proposal: true })
    ]);
    const pageKey = ticketStatusGroupKey(
      ticketWithFiledProposal({ assigned: true })
    );
    const pageLabel = TICKET_STATUS_GROUPS.find((group) => group.key === pageKey)?.label;
    expect(railGroups.map((group) => group.label)).toEqual(["Yours"]);
    expect(pageLabel).toBe("Yours");
  });
});
