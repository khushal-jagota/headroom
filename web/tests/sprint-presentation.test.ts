import { describe, expect, it } from "vitest";
import {
  outcomeTicketProgress,
  sprintDayLabel,
  sprintProjectGroups,
  sprintTicketCondition,
  sprintTicketSectionsForTickets,
  type SprintTicket
} from "../src/lib/sprintPresentation";
import type { SprintOutcomeGroup, SprintTicketSummary } from "../src/lib/types";

describe("Sprint day presentation", () => {
  const weeklySprint = { date_start: "2026-08-03", date_end: "2026-08-09" };

  it("reports each inclusive day of a weekly sprint from the supplied planning date", () => {
    expect(sprintDayLabel(weeklySprint, "2026-08-03")).toBe("day 1 of 7");
    expect(sprintDayLabel(weeklySprint, "2026-08-06")).toBe("day 4 of 7");
    expect(sprintDayLabel(weeklySprint, "2026-08-09")).toBe("day 7 of 7");
  });

  it("rejects invalid dates and planning dates outside the sprint", () => {
    expect(sprintDayLabel(weeklySprint, "2026-08-10")).toBe("");
    expect(sprintDayLabel({ date_start: "bad", date_end: "2026-08-09" }, "2026-08-03")).toBe("");
  });
});

const ticket = (overrides: Partial<SprintTicketSummary> = {}): SprintTicketSummary => ({
  id: "t_default",
  title: "Default",
  stage: "needs_success",
  priority: "P2",
  ticket_status: "empty",
  project_id: "project_panels",
  sprint_item_id: "si_default",
  waiting_to_closeout: false,
  awaiting_reply: false,
  awaiting_approval: false,
  assigned: false,
  agent_state: "idle",
  ...overrides
});

const item = (overrides: Partial<SprintOutcomeGroup> = {}): SprintOutcomeGroup => ({
  outcome: { id: "si_default", title: "Default outcome", priority: "P2", deadline: null, project_id: "project_panels", project: "Panels", created_at: 1, updated_at: 1, awaiting_reply: false, awaiting_approval: false, assigned: false, agent_state: "idle" },
  committed: true,
  tickets: [],
  ...overrides
});

describe("Sprint ticket conditions", () => {
  it.each([
    [ticket({ stage: "done", ticket_status: "errored" }), "completed", "done"],
    [ticket({ ticket_status: "blocked" }), "errored", "blocked"],
    [ticket({ ticket_status: "errored", agent_state: "errored" }), "errored", "errored"],
    [ticket({ assigned: true }), "current-assigned", "assigned"],
    [ticket({ ticket_status: "awaiting_approval", awaiting_approval: true }), "current-awaiting-approval", "to review"],
    [ticket({ awaiting_reply: true }), "needs-me", "need you"],
    [ticket({ ticket_status: "agent", agent_state: "working" }), "current-running", "working"],
    [ticket({ waiting_to_closeout: true }), "current-waiting", "waiting for closeout"],
    [ticket(), "upcoming", "to do"]
  ])("maps %o to %s and %s", (input, mark, word) => {
    expect(sprintTicketCondition(input)).toEqual({ mark, word });
  });
});

describe("Outcome presentation", () => {
  it("shows only the done and total counts, including an empty Outcome", () => {
    expect(outcomeTicketProgress(item())).toBe("0/0");
    expect(outcomeTicketProgress(item({ tickets: [ticket()] }))).toBe("0/1");
    expect(outcomeTicketProgress(item({ tickets: [ticket({ stage: "done" }), ticket()] }))).toBe("1/2");
  });

  it("presents unclassified Sprint Tickets without a Sprint Item identity", () => {
    const today = ticket({ id: "t_today", priority: "P2" });
    const later = ticket({ id: "t_later", priority: "P1" });
    const dropped = ticket({ id: "t_dropped", stage: "dropped" });
    expect(sprintTicketSectionsForTickets([today, later, dropped], new Set([today.id]))).toEqual({
      today: [today],
      later: [later],
      done: []
    });
  });

  it("excludes dropped Tickets from sections and rollups", () => {
    const value = item({ tickets: [ticket({ stage: "done" }), ticket({ id: "t_drop", stage: "dropped" })] });
    expect(outcomeTicketProgress(value)).toBe("1/1");
    expect(sprintTicketSectionsForTickets(value.tickets || [], new Set())).toEqual({
      today: [],
      later: [],
      done: [value.tickets?.[0]]
    });
  });

  it("separates live, off-today, and done work with blocked live work last", () => {
    // Errored and blocked read as different words. Both still sink to the end.
    const blocked = ticket({ id: "t_blocked", priority: "P0", ticket_status: "blocked" });
    const errored = ticket({ id: "t_errored", priority: "P0", ticket_status: "errored", agent_state: "errored" });
    const moving = ticket({ id: "t_moving", priority: "P2", ticket_status: "agent", agent_state: "working" });
    const later = ticket({ id: "t_later", priority: "P1" });
    const done = ticket({ id: "t_done", stage: "done", priority: "P3" });
    expect(
      sprintTicketSectionsForTickets(
        [blocked, errored, moving, later, done],
        new Set([blocked.id, errored.id, moving.id])
      )
    ).toEqual({ today: [moving, blocked, errored], later: [later], done: [done] });
  });
});

describe("Sprint Project overview", () => {
  it("orders assessed Projects, uses the stable fallback, and puts Other last", () => {
    const groups = sprintProjectGroups(
      [
        item({ outcome: { ...item().outcome, id: "si_other", project_id: "project_other", project: "Other" } }),
        item({ outcome: { ...item().outcome, id: "si_panels", project_id: "project_panels", project: "Panels" } }),
        item({ outcome: { ...item().outcome, id: "si_vylo", project_id: "project_vylo", project: "Vylo" } })
      ],
      [
        { id: "project_other", name: "Other", summary: "", priority: null, created_at: 1, updated_at: 1 },
        { id: "project_panels", name: "Panels", summary: "", priority: "P0", created_at: 1, updated_at: 1 },
        { id: "project_vylo", name: "Vylo", summary: "", priority: null, created_at: 1, updated_at: 1 }
      ]
    );
    expect(groups.map((group) => group.label)).toEqual(["Panels", "Vylo", "Other"]);
  });

  it("orders Outcomes by priority and creation rather than aggregate completion", () => {
    const normal = item({ outcome: { ...item().outcome, id: "si_normal", priority: "P3" } });
    const older = item({ outcome: { ...item().outcome, id: "si_older", priority: "P0", created_at: 1 }, tickets: [ticket({ stage: "done" })] });
    const newer = item({ outcome: { ...item().outcome, id: "si_newer", priority: "P0", created_at: 2 } });
    const [group] = sprintProjectGroups([normal, newer, older], []);
    expect(group.outcomes.map((entry) => entry.outcome.id)).toEqual(["si_older", "si_newer", "si_normal"]);
  });
});
