import { describe, expect, it } from "vitest";
import {
  sprintItemIsDone,
  sprintItemRollup,
  sprintProjectGroups,
  sprintTicketCondition,
  sprintTicketSections,
  type SprintItem,
  type SprintTicket
} from "../src/lib/sprintPresentation";

const ticket = (overrides: Partial<SprintTicket> = {}): SprintTicket => ({
  id: "t_default",
  title: "Default",
  stage: "needs_success",
  priority: "P2",
  ticket_status: "empty",
  ...overrides
});

const item = (overrides: Partial<SprintItem> = {}): SprintItem => ({
  id: "si_default",
  title: "Default item",
  priority: "P2",
  project_id: "project_panels",
  project: "Panels",
  kind: "normal",
  status: "todo",
  tickets: [],
  ...overrides
});

describe("Sprint ticket conditions", () => {
  it.each([
    [ticket({ stage: "done", ticket_status: "errored" }), "completed", "done"],
    [ticket({ ticket_status: "blocked" }), "errored", "blocked"],
    [ticket({ ticket_status: "errored" }), "errored", "blocked"],
    [ticket({ has_pending_proposal: true }), "current-awaiting-approval", "to review"],
    [ticket({ ticket_status: "awaiting_approval" }), "current-awaiting-approval", "to review"],
    [ticket({ ticket_status: "needs_user" }), "needs-me", "need you"],
    [ticket({ ticket_status: "user" }), "needs-me", "yours"],
    [ticket({ ticket_status: "agent" }), "current-running", "working"],
    [ticket({ ticket_status: "paired" }), "current-paired", "paired"],
    [ticket(), "upcoming", "to do"]
  ])("maps %o to %s and %s", (input, mark, word) => {
    expect(sprintTicketCondition(input)).toEqual({ mark, word });
  });
});

describe("Sprint Item presentation", () => {
  it("uses words for empty and untouched items, a fraction for progress, and done when settled", () => {
    expect(sprintItemRollup(item())).toBe("to do");
    expect(sprintItemRollup(item({ tickets: [ticket()] }))).toBe("to do");
    expect(sprintItemRollup(item({ tickets: [ticket({ stage: "done" }), ticket()] }))).toBe("1/2");
    const settled = item({ tickets: [ticket({ stage: "done" }), ticket({ id: "t_2", stage: "done" })] });
    expect(sprintItemRollup(settled)).toBe("done");
    expect(sprintItemIsDone(settled)).toBe(true);
  });

  it("excludes dropped Tickets from sections and rollups", () => {
    const value = item({ tickets: [ticket({ stage: "done" }), ticket({ id: "t_drop", stage: "dropped" })] });
    expect(sprintItemRollup(value)).toBe("done");
    expect(sprintTicketSections(value, new Set())).toEqual({ today: [], later: [], done: [value.tickets?.[0]] });
  });

  it("separates live, off-today, and done work with blocked live work last", () => {
    const blocked = ticket({ id: "t_blocked", priority: "P0", ticket_status: "blocked" });
    const moving = ticket({ id: "t_moving", priority: "P2", ticket_status: "agent" });
    const later = ticket({ id: "t_later", priority: "P1" });
    const done = ticket({ id: "t_done", stage: "done", priority: "P3" });
    expect(
      sprintTicketSections(item({ tickets: [blocked, moving, later, done] }), new Set([blocked.id, moving.id]))
    ).toEqual({ today: [moving, blocked], later: [later], done: [done] });
  });
});

describe("Sprint Project overview", () => {
  it("orders assessed Projects, uses the stable fallback, and puts Other last", () => {
    const groups = sprintProjectGroups(
      [
        item({ id: "si_other", project_id: "project_other", project: "Other" }),
        item({ id: "si_panels", project_id: "project_panels", project: "Panels" }),
        item({ id: "si_vylo", project_id: "project_vylo", project: "Vylo" })
      ],
      [
        { id: "project_other", name: "Other", summary: "", priority: null, created_at: 1, updated_at: 1 },
        { id: "project_panels", name: "Panels", summary: "", priority: "P0", created_at: 1, updated_at: 1 },
        { id: "project_vylo", name: "Vylo", summary: "", priority: null, created_at: 1, updated_at: 1 }
      ]
    );
    expect(groups.map((group) => group.label)).toEqual(["Panels", "Vylo", "Other"]);
  });

  it("puts completed and fallback Items last within each Project", () => {
    const normal = item({ id: "si_normal", priority: "P3" });
    const fallback = item({ id: "si_fallback", priority: "P0", kind: "other" });
    const done = item({ id: "si_done", priority: "P0", tickets: [ticket({ stage: "done" })] });
    const [group] = sprintProjectGroups([fallback, done, normal], []);
    expect(group.items.map((entry) => entry.id)).toEqual(["si_normal", "si_fallback", "si_done"]);
  });
});
