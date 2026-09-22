import { describe, expect, it } from "vitest";
import {
  feedbackLabel,
  homeDotLabel,
  homeTiles,
  sprintItemHeading,
  sprintItemWord,
  ticketFacts,
  ticketPageMark,
  workspaceHeading
} from "./support/screenReadings";

// Whose Ticket it is has one answer, `assigned`, written by `ticket_is_assigned` on the
// server from the ownership mode of the Ticket's current Stage. These tests hold every
// screen to that one answer, so no screen can go back to deciding it for itself.
describe("a Stage the worker owns", () => {
  // Thirteen of the fourteen shipped Worker types declare the Brief worker-owned, so
  // this is what an ordinary agent Ticket looks like while a worker writes its Brief.
  const workerOwned = ticketFacts({ stage: "needs_brief", assigned: false });

  it("is never named as the owner's work", () => {
    expect(workspaceHeading(workerOwned)).not.toBe("Yours");
    expect(sprintItemHeading(workerOwned)).not.toBe("Yours");
    expect(sprintItemWord(workerOwned)).not.toBe("yours");
    expect(feedbackLabel(workerOwned)).not.toBe("Yours");
    expect(homeTiles(workerOwned)).not.toContain("Assigned 1");
    expect(homeDotLabel(workerOwned)).not.toBe("Assigned");
    expect(ticketPageMark(workerOwned)).not.toBe("current-assigned");
  });

  it("reads as an empty Stage instead", () => {
    expect(workspaceHeading(workerOwned)).toBe("Empty");
    expect(sprintItemWord(workerOwned)).toBe("empty");
    expect(ticketPageMark(workerOwned)).toBe("current-waiting");
  });
});

describe("a Stage the owner owns", () => {
  const userOwned = ticketFacts({ stage: "needs_brief", assigned: true });

  it("reads as his on every screen that says so", () => {
    expect(workspaceHeading(userOwned)).toBe("Yours");
    expect(sprintItemHeading(userOwned)).toBe("Yours");
    expect(sprintItemWord(userOwned)).toBe("yours");
    expect(feedbackLabel(userOwned)).toBe("Yours");
    expect(homeTiles(userOwned)).toEqual(["Assigned 1"]);
    expect(homeDotLabel(userOwned)).toBe("No current activity");
    expect(ticketPageMark(userOwned)).toBe("current-assigned");
  });

  it("still yields to a proposal he has to approve", () => {
    const parked = ticketFacts({ assigned: true, awaiting_approval: true });
    expect(workspaceHeading(parked)).toBe("Needs your approval");
    expect(feedbackLabel(parked)).toBe("Needs your approval");
  });
});
