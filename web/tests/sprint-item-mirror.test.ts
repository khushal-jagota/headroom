import { describe, expect, it } from "vitest";

import type {
  BoardCard,
  BoardSprintItem,
  SprintItemWorkspace,
  SprintItemWorkspaceTicket
} from "../src/lib/types";
import {
  WORK_ITEM_TICKET_GROUPS,
  workItemActivityMark,
  workItemActivityMarkPresentation,
  workItemTicketGroupKey,
  workItemTicketGroups,
  type WorkItemTicketFacts
} from "../src/lib/workItemPresentation";
import { buildWorkspaceRail } from "../src/lib/workspaceRail";
import { dayVisualTicket } from "../src/lib/dayPresentation";
import { feedbackTicketStageState } from "../src/lib/feedback";
import type { DayTicket } from "../src/lib/types";

function ticket(
  id: string,
  values: Partial<SprintItemWorkspaceTicket> = {}
): SprintItemWorkspaceTicket {
  return {
    id,
    title: id,
    stage: "needs_implementation",
    priority: "P1",
    activity_at: 1,
    ticket_status: "empty",
    waiting_to_closeout: false,
    has_pending_proposal: false,
    gating_field: "implementation",
    blocked: false,
    worker_type: "coding",
    day_ids: [],
    sprint_id: null,
    sprint_name: null,
    awaiting_reply: false,
    awaiting_answer: false,
    awaiting_approval: false,
    awaiting_agent_approval: false,
    assigned: false,
    agent_state: "idle",
    ...values
  };
}

function boardCard(row: SprintItemWorkspaceTicket, itemId = "si_mirror"): BoardCard {
  return {
    ...row,
    deadline: null,
    project_id: null,
    project: null,
    group_project_id: null,
    group_project: null,
    stage_label: "Implementation",
    gating_field_label: "Implementation",
    is_done: row.stage === "done",
    conversation_id: null,
    employee_backend: "codex",
    sprint_item_id: itemId,
    sprint_item_title: "Mirror Item",
    sprint_item_priority: "P1"
  };
}

const quiet = {
  awaiting_reply: false,
  awaiting_answer: false,
  awaiting_approval: false,
  awaiting_agent_approval: false,
  assigned: false,
  agent_state: "idle" as const
};

function workspace(rows: SprintItemWorkspaceTicket[]): SprintItemWorkspace {
  return {
    id: "si_mirror",
    title: "Mirror Item",
    body: "",
    priority: "P1",
    deadline: null,
    project_id: "project_panels",
    project: "Panels",
    kind: "normal",
    created_at: 1,
    updated_at: 1,
    committed_sprints: [],
    supervisor: {
      agent_key: "sprint_item:si_mirror",
      conversation_id: null,
      launch_configuration: {
        employee_backend: "codex",
        employee_launch_model: "gpt-5.6-sol",
        employee_launch_reasoning_effort: "medium"
      }
    },
    planning_day_id: "day_2026-09-22",
    today_ticket_ids: [],
    tickets: rows,
    artifacts: [],
    conversation_history: [],
    ticket_rollup: quiet,
    ...quiet
  };
}

describe("the shared Item and Ticket presentation", () => {
  it.each([
    [{ awaiting_approval: true }, "awaiting_approval"],
    [{ awaiting_answer: true }, "awaiting_answer"],
    [{ assigned: true }, "assigned"],
    [{ awaiting_reply: true }, "awaiting_reply"],
    [{ agent_state: "errored" }, "errored"],
    [{ ticket_status: "agent" }, "agent"],
    [{ waiting_to_closeout: true }, "waiting_to_closeout"],
    [{ awaiting_agent_approval: true }, "status_awaiting_approval"],
    [{ ticket_status: "empty" }, "empty"],
    [{ ticket_status: "blocked" }, "blocked"],
    [{ stage: "done" }, "done"]
  ] as const)("classifies %o as %s", (values, expected) => {
    expect(workItemTicketGroupKey(ticket("state", values))).toBe(expected);
  });

  it("uses the approved group order for overlaps", () => {
    expect(
      workItemTicketGroupKey(
        ticket("overlap", {
          stage: "done",
          awaiting_answer: true,
          awaiting_reply: true,
          agent_state: "errored"
        })
      )
    ).toBe("awaiting_answer");
    expect(
      workItemTicketGroupKey(ticket("broken-approval", {
        awaiting_approval: true,
        agent_state: "errored"
      }))
    ).toBe("awaiting_approval");
  });

  it("keeps ownership independent from the universal activity mark", () => {
    const assignedAndWorking = ticket("assigned-working", {
      assigned: true,
      agent_state: "working"
    });
    expect(workItemTicketGroupKey(assignedAndWorking)).toBe("assigned");
    expect(workItemActivityMark(assignedAndWorking)).toBe("working");
    expect(workItemActivityMarkPresentation("working").state).toBe("current-running");
  });

  it("uses white, blue, spinner, then empty precedence", () => {
    expect(workItemActivityMark(ticket("all", {
      awaiting_approval: true,
      awaiting_answer: true,
      awaiting_reply: true,
      agent_state: "working"
    }))).toBe("owner-approval");
    expect(workItemActivityMark(ticket("answer", {
      awaiting_answer: true,
      awaiting_reply: true,
      agent_state: "working"
    }))).toBe("owner-answer");
    expect(workItemActivityMark(ticket("reply", {
      awaiting_reply: true,
      agent_state: "working"
    }))).toBe("reply");
    expect(workItemActivityMark(ticket("working", { agent_state: "working" }))).toBe("working");
    expect(workItemActivityMark(ticket("quiet"))).toBeNull();
  });

  it("uses the same activity mark on Day and Feedback Ticket rows", () => {
    const approval = ticket("approval", {
      awaiting_approval: true,
      awaiting_reply: true,
      agent_state: "working"
    });
    const reply = ticket("reply", { awaiting_reply: true, agent_state: "working" });
    const working = ticket("working", { assigned: true, agent_state: "working" });
    expect(dayVisualTicket(approval as unknown as DayTicket).state).toBe("needs-me");
    expect(feedbackTicketStageState(approval)).toBe("needs-me");
    expect(dayVisualTicket(reply as unknown as DayTicket).state).toBe(
      "current-awaiting-approval"
    );
    expect(feedbackTicketStageState(reply)).toBe("current-awaiting-approval");
    expect(dayVisualTicket(working as unknown as DayTicket).state).toBe("current-running");
    expect(feedbackTicketStageState(working)).toBe("current-running");
  });

  it("returns identical groups, counts, marks, and activity order for both payload shapes", () => {
    const rows = [
      ticket("older", { assigned: true, agent_state: "working", activity_at: 10 }),
      ticket("newer", { assigned: true, agent_state: "working", activity_at: 20 }),
      ticket("reply", { awaiting_reply: true, activity_at: 30 })
    ];
    const fromWorkspace = workItemTicketGroups(rows);
    const fromBoard = workItemTicketGroups(rows.map((row) => boardCard(row)));
    const reading = (groups: readonly {
      key: string;
      label: string;
      tickets: readonly WorkItemTicketFacts[];
    }[]) => groups.map((group) => ({
      key: group.key,
      label: group.label,
      count: group.tickets.length,
      tickets: group.tickets.map((row) => ({ id: row.id, mark: workItemActivityMark(row) }))
    }));
    expect(reading(fromWorkspace)).toEqual(reading(fromBoard));
    expect(fromWorkspace.find((group) => group.key === "assigned")?.tickets.map((row) => row.id))
      .toEqual(["newer", "older"]);
    expect(fromWorkspace.map((group) => group.label)).toEqual([
      "Yours",
      "Messages"
    ]);
  });

  it("uses the selected workspace snapshot for both the rail Item and its children", () => {
    const stale = ticket("same", { assigned: true, agent_state: "idle", activity_at: 1 });
    const current = ticket("same", { assigned: true, agent_state: "working", activity_at: 2 });
    const selected = workspace([current]);
    selected.ticket_rollup = { ...quiet, assigned: true, agent_state: "working" };
    const summary: BoardSprintItem = {
      id: selected.id,
      created_at: selected.created_at,
      conversation_id: null,
      ticket_rollup: { ...quiet, assigned: true, agent_state: "idle" },
      ...quiet
    };
    const rail = buildWorkspaceRail([boardCard(stale)], [summary], selected);
    expect(rail.items[0].mark).toBe("working");
    expect(rail.items[0].groups[0].cards[0].agent_state).toBe("working");

    const direct = buildWorkspaceRail([], [], selected);
    expect(direct.items.map((item) => item.id)).toEqual([selected.id]);
  });

  it("publishes the exact approved group order", () => {
    expect(WORK_ITEM_TICKET_GROUPS.map((group) => group.key)).toEqual([
      "awaiting_approval",
      "awaiting_answer",
      "assigned",
      "awaiting_reply",
      "errored",
      "agent",
      "waiting_to_closeout",
      "status_awaiting_approval",
      "empty",
      "blocked",
      "done"
    ]);
  });
});
