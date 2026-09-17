import { describe, expect, it } from "vitest";
import {
  feedbackPageContext,
  feedbackPageHref,
  feedbackRelativeTime,
  feedbackTicketStageState,
  feedbackTicketStateLabel
} from "../src/lib/feedback";

describe("feedback page context", () => {
  it("labels ordinary screens and preserves their exact addresses", () => {
    expect(feedbackPageContext("#/review")).toEqual({ address: "#/review", label: "Review" });
    expect(feedbackPageContext("#/scheduled-tasks")).toEqual({
      address: "#/scheduled-tasks",
      label: "Scheduled tasks"
    });
  });

  it("adds the loaded title for Ticket and Sprint Item contexts", () => {
    expect(feedbackPageContext("#/workspace/t_1?view=items", "Fix focus")).toEqual({
      address: "#/workspace/t_1?view=items",
      label: "Ticket · Fix focus"
    });
    expect(feedbackPageContext("#/workspace/item/i_1", "Capture feedback").label).toBe(
      "Sprint Item · Capture feedback"
    );
    expect(feedbackPageContext("#/sprint?item=i_1", "Capture feedback").label).toBe(
      "Sprint Item · Capture feedback"
    );
  });

  it("only turns internal Panels hashes into page links", () => {
    expect(feedbackPageHref("#/workspace/t_1?view=items")).toBe("#/workspace/t_1?view=items");
    expect(feedbackPageHref("javascript:alert(1)")).toBeNull();
    expect(feedbackPageHref("https://example.com")).toBeNull();
  });
});

describe("feedback presentation", () => {
  it("uses the approved relative-time ladder", () => {
    const now = new Date("2026-09-11T12:00:00Z");
    expect(feedbackRelativeTime(Date.parse("2026-09-11T11:48:00Z") / 1000, now)).toBe("12 min ago");
    expect(feedbackRelativeTime(Date.parse("2026-09-11T10:00:00Z") / 1000, now)).toBe("2 h ago");
    expect(feedbackRelativeTime(Date.parse("2026-09-10T15:00:00Z") / 1000, now)).toBe("Yesterday");
    expect(feedbackRelativeTime(Date.parse("2026-09-08T15:00:00Z") / 1000, now)).toBe("Sep 8");
  });

  it("maps ticket workflow facts onto the shared stage mark", () => {
    const ticket = { id: "t_1", title: "Fix focus", stage: "needs_implementation", ticket_status: "agent", awaiting_reply: false, awaiting_approval: false, assigned: false, agent_state: "working" as const };
    expect(feedbackTicketStageState(ticket)).toBe("current-running");
    expect(feedbackTicketStateLabel(ticket)).toBe("Running");
    expect(feedbackTicketStageState({ ...ticket, stage: "done", ticket_status: "empty" })).toBe("completed");
    expect(feedbackTicketStageState({ ...ticket, ticket_status: "blocked" })).toBe("errored");
    expect(feedbackTicketStateLabel({ ...ticket, ticket_status: "blocked" })).toBe("Blocked");
    expect(feedbackTicketStageState({ ...ticket, awaiting_reply: true })).toBe("needs-me");
    expect(feedbackTicketStageState({ ...ticket, ticket_status: "empty", agent_state: "idle" })).toBe("upcoming");
    expect(feedbackTicketStageState({ ...ticket, stage: "needs_closeout", ticket_status: "empty", agent_state: "idle" })).toBe("current-waiting");
  });
});
