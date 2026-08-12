import { describe, expect, it } from "vitest";

import {
  dayActionTiles,
  dayDotOrder,
  dayPageState,
  dayVisualTicket
} from "../src/lib/dayPresentation";
import type { DayTicket } from "../src/lib/types";

function ticket(
  id: string,
  values: Partial<DayTicket> = {}
): DayTicket {
  return {
    id,
    title: id,
    stage: "needs_implementation",
    ticket_status: "empty",
    conversation_id: null,
    ...values
  };
}

describe("Day presentation", () => {
  it("maps every live ticket state and builds the action summary", () => {
    const visuals = [
      ticket("done", { stage: "done", is_done: true }),
      ticket("working", { ticket_status: "agent", agent_working: true }),
      ticket("paired", { ticket_status: "paired" }),
      ticket("review", { ticket_status: "awaiting_user_review" }),
      ticket("needs-you", { needs_me: true }),
      ticket("upcoming")
    ].map((item) => dayVisualTicket(item, {}));

    expect(visuals.map((item) => item.state)).toEqual([
      "completed",
      "current-running",
      "current-paired",
      "current-awaiting-approval",
      "needs-me",
      "upcoming"
    ]);
    expect(dayPageState(visuals)).toBe("populated");
    expect(dayActionTiles(visuals)).toEqual([
      { key: "needs-me", label: "Need you", count: 1, href: "#/workspace" },
      { key: "review", label: "To review", count: 1, href: "#/review" },
      { key: "working", label: "Working", count: 1, href: "#/workspace" },
      { key: "paired", label: "Paired", count: 1, href: "#/workspace" },
      { key: "done", label: "Done", count: 1, href: "#/workspace" }
    ]);
  });

  it("orders the progress dots by state and keeps roster order inside a run", () => {
    const visuals = [
      ticket("done-1", { stage: "done", is_done: true }),
      ticket("working-1", { ticket_status: "agent", agent_working: true }),
      ticket("upcoming-1"),
      ticket("review-1", { ticket_status: "awaiting_user_review" }),
      ticket("needs-you-1", { needs_me: true }),
      ticket("paired-1", { ticket_status: "paired" }),
      ticket("done-2", { stage: "done", is_done: true }),
      ticket("working-2", { ticket_status: "agent", agent_working: true })
    ].map((item) => dayVisualTicket(item, {}));

    expect(dayDotOrder(visuals).map((item) => item.ticket.id)).toEqual([
      "needs-you-1",
      "review-1",
      "paired-1",
      "working-1",
      "working-2",
      "upcoming-1",
      "done-1",
      "done-2"
    ]);
    expect(visuals.map((item) => item.ticket.id)).toEqual([
      "done-1",
      "working-1",
      "upcoming-1",
      "review-1",
      "needs-you-1",
      "paired-1",
      "done-2",
      "working-2"
    ]);
  });

  it("keeps a day calm when no ticket needs the user", () => {
    const visuals = [
      ticket("done", { stage: "done", is_done: true }),
      ticket("working", { ticket_status: "agent", agent_working: true }),
      ticket("paired", { ticket_status: "paired" })
    ].map((item) => dayVisualTicket(item, {}));

    expect(dayPageState(visuals)).toBe("calm");
    expect(dayActionTiles(visuals).map((item) => item.key)).toEqual([
      "working",
      "paired",
      "done"
    ]);
  });

  it("uses the browser watermark to quiet an agent reply", () => {
    const item = ticket("reply", {
      conversation_id: "conv-1",
      latest_turn_ended_sequence: 8
    });

    expect(dayVisualTicket(item, {}).state).toBe("current-awaiting-approval");
    expect(dayVisualTicket(item, { "conv-1": 8 }).state).toBe("upcoming");
  });
});
