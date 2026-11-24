import { WORKER_STATUS_LABEL, ticketStatusLabel } from "../../server/tickets/logic/status_presentation";
import { describe, expect, it } from "vitest";
import {
  managerHeading,
  ticketFacts,
  ticketPageMark,
  workspaceHeading
} from "./support/screenReadings";

// Whether a worker has a Ticket has one answer, `agentHoldsTicket`: the durable claim,
// or a turn running now. These tests hold every screen to that one answer.
describe("a worker that holds the claim but is between turns", () => {
  // The wakeup system writes `agent_owned` when it sends a worker its step, and the claim
  // stands until the step comes back. A worker that ends its turn to wait on a long job
  // is in exactly this state, which happens constantly.
  const held = ticketFacts({ ticket_status: "agent_owned", agent_state: "idle" });

  it("still reads as the agent's on every screen", () => {
    expect(workspaceHeading(held)).toBe(WORKER_STATUS_LABEL);
    expect(managerHeading(held)).toBe(WORKER_STATUS_LABEL);
    expect(ticketStatusLabel(held.ticket_status)).toBe(WORKER_STATUS_LABEL);
    // New clients can reach an old server during rollout and read old receipt snapshots.
    const legacy = ticketFacts({ ticket_status: "agent", agent_state: "idle" });
    expect(workspaceHeading(legacy)).toBe(WORKER_STATUS_LABEL);
    expect(managerHeading(legacy)).toBe(WORKER_STATUS_LABEL);
    expect(ticketPageMark(legacy)).toBe("current-running");
    expect(ticketStatusLabel(legacy.ticket_status)).toBe(WORKER_STATUS_LABEL);
  });

  it("does not read as an untouched Stage on its own page", () => {
    expect(ticketPageMark(held)).toBe("current-running");
  });
});

describe("a live turn with no claim behind it", () => {
  const running = ticketFacts({ ticket_status: "empty", agent_state: "working" });

  it("reads the same, so neither fact alone is the carrier", () => {
    expect(workspaceHeading(running)).toBe(WORKER_STATUS_LABEL);
    expect(ticketPageMark(running)).toBe("current-running");
  });
});

describe("a Ticket no worker has", () => {
  const untouched = ticketFacts({ ticket_status: "empty", agent_state: "idle" });

  it("is named as the agent's nowhere", () => {
    expect(workspaceHeading(untouched)).toBe("Empty");
    expect(ticketPageMark(untouched)).toBe("current-waiting");
  });
});

describe("a broken worker", () => {
  // Errored outranks the claim everywhere, so the reader is told the worker stopped
  // rather than that it is still going.
  const broken = ticketFacts({ ticket_status: "agent_owned", agent_state: "errored" });

  it("is named before the work it holds", () => {
    expect(workspaceHeading(broken)).toBe("Errored");
    expect(ticketPageMark(broken)).toBe("errored");
  });
});
