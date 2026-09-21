import { describe, expect, it } from "vitest";
import {
  feedbackLabel,
  feedbackMark,
  homeDotLabel,
  homeTiles,
  sprintItemHeading,
  sprintItemWord,
  ticketFacts,
  ticketPageMark,
  workspaceHeading
} from "./support/screenReadings";

// Whether a worker has a Ticket has one answer, `agentHoldsTicket`: the durable claim,
// or a turn running now. These tests hold every screen to that one answer.
describe("a worker that holds the claim but is between turns", () => {
  // The wakeup system writes `agent` when it sends a worker its step, and the claim
  // stands until the step comes back. A worker that ends its turn to wait on a long job
  // is in exactly this state, which happens constantly.
  const held = ticketFacts({ ticket_status: "agent", agent_state: "idle" });

  it("still reads as the agent's on every screen", () => {
    expect(workspaceHeading(held)).toBe("Agent");
    expect(sprintItemHeading(held)).toBe("Agent");
    expect(sprintItemWord(held)).toBe("agent");
    expect(feedbackLabel(held)).toBe("Agent");
    expect(feedbackMark(held)).toBe("upcoming");
    expect(homeTiles(held)).toEqual([]);
    expect(homeDotLabel(held)).toBe("No current activity");
  });

  it("does not read as an untouched Stage on its own page", () => {
    expect(ticketPageMark(held)).toBe("current-running");
  });
});

describe("a live turn with no claim behind it", () => {
  const running = ticketFacts({ ticket_status: "empty", agent_state: "working" });

  it("reads the same, so neither fact alone is the carrier", () => {
    expect(workspaceHeading(running)).toBe("Agent");
    expect(sprintItemWord(running)).toBe("agent");
    expect(feedbackLabel(running)).toBe("Agent");
    expect(homeTiles(running)).toEqual(["Working 1"]);
    expect(ticketPageMark(running)).toBe("current-running");
  });
});

describe("a Ticket no worker has", () => {
  const untouched = ticketFacts({ ticket_status: "empty", agent_state: "idle" });

  it("is named as the agent's nowhere", () => {
    expect(workspaceHeading(untouched)).toBe("Empty");
    expect(sprintItemWord(untouched)).toBe("empty");
    expect(feedbackLabel(untouched)).not.toBe("Agent");
    expect(homeTiles(untouched)).toEqual([]);
    expect(ticketPageMark(untouched)).toBe("current-waiting");
  });
});

describe("a broken worker", () => {
  // Errored outranks the claim everywhere, so the reader is told the worker stopped
  // rather than that it is still going.
  const broken = ticketFacts({ ticket_status: "agent", agent_state: "errored" });

  it("is named before the work it holds", () => {
    expect(workspaceHeading(broken)).toBe("Errored");
    expect(feedbackLabel(broken)).toBe("Errored");
    expect(ticketPageMark(broken)).toBe("errored");
  });
});
