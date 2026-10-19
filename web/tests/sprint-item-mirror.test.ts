import { conversationSignalReading } from "./support/conversationSignalReading";
import { describe, expect, it } from "vitest";

import type {
  BoardCard,
  BoardSprintItem,
  SprintItemWorkspace,
  SprintItemWorkspaceTicket
} from "../src/lib/types";
import {
  workItemTicketGroupKey,
  workItemTicketGroups,
  type WorkItemTicketFacts
} from "../src/lib/workItemPresentation";
import { buildWorkspaceRail } from "../src/lib/workspaceRail";
import { sprintTicketCondition } from "../src/lib/sprintPresentation";
import { workspaceHeading, sprintItemHeading, sprintItemWord, ticketFacts } from "./support/screenReadings";
import { dayActionTiles, dayVisualTicket } from "../src/lib/dayPresentation";
import { feedbackTicketStateLabel } from "../src/lib/feedback";
import type { DayTicket } from "../src/lib/types";

function ticket(
  id: string,
  values: Partial<SprintItemWorkspaceTicket> = {}
): SprintItemWorkspaceTicket {
  return {
    id,
    title: id,
    recap: "",recap_preview: "",
    stage: "needs_implementation",
    activity_at: 1,
    is_backlogged: false,
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
    goal: "",
    recap: "",
    project_id: "project_panels",
    project: "Panels",
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

function boardItem(rows: SprintItemWorkspaceTicket[]): BoardSprintItem {
  const full = workspace(rows);
  return { id: full.id, title: full.title, recap: "",recap_preview: "",
    created_at: full.created_at, conversation_id: null, tickets: rows,
    ticket_rollup: quiet, ...quiet };
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

  it.each([
    [{ awaiting_approval: true, has_pending_proposal: true }, "awaiting_approval"],
    [{ awaiting_agent_approval: true, has_pending_proposal: true }, "status_awaiting_approval"],
    [{ awaiting_answer: true }, "awaiting_answer"], [{ assigned: true }, "assigned"], [{ awaiting_reply: true }, "awaiting_reply"],
    [{ agent_state: "errored" }, "errored"], [{ agent_state: "working" }, "agent"],
    [{ waiting_to_closeout: true }, "waiting_to_closeout"], [{ ticket_status: "blocked" }, "blocked"],
    [{ stage: "done" }, "done"]
  ] as const)("gives supplied backlogged work one group while preserving %o", (facts, resumedGroup) => {
    const row = ticket("backlogged", { ...facts, is_backlogged: true });
    const original = { ...row };
    const groups = workItemTicketGroups([row]);
    expect(groups.map((group) => group.key)).toEqual(["backlog"]);
    expect(groups[0].tickets).toEqual([original]);
    expect(groups[0].tickets[0]).toBe(row);
    expect(groups[0].quiet).toBe(false);
    expect(sprintTicketCondition(row).word).toBe("backlog");
    expect(workItemTicketGroupKey({ ...row, is_backlogged: false })).toBe(resumedGroup);
  });

  it("forwards explicit backlog through each shared screen adapter", () => {
    const facts = ticketFacts({ is_backlogged: true, awaiting_approval: true });
    expect(workspaceHeading(facts)).toBe("Backlog");
    expect(sprintItemHeading(facts)).toBe("Backlog");
    expect(sprintItemWord(facts)).toBe("backlog");
    const { is_backlogged: _, ...legacy } = ticket("legacy", { awaiting_approval: true });
    expect(workItemTicketGroupKey(legacy)).toBe("awaiting_approval");
  });

  it("preserves historical Day counts and Feedback labels with raw backlog facts", () => {
    const rows = [ticket("review", { is_backlogged: true, awaiting_approval: true }),
      ticket("done", { is_backlogged: true, stage: "done" })];
    const visuals = rows.map((row) => dayVisualTicket(row as unknown as DayTicket));
    expect(visuals.map((visual) => visual.group)).toEqual(["awaiting_approval", "done"]);
    expect(dayActionTiles(visuals).map(({ key, count }) => [key, count]))
      .toEqual([["review", 1], ["done", 1]]);
    expect(rows.map(feedbackTicketStateLabel)).toEqual(["Needs your approval", "Done"]);
    expect(rows.map((row) => workItemTicketGroupKey(row))).toEqual(["backlog", "backlog"]);
  });

  it("uses projected completion and failure facts in the Item, rail, Day, and Feedback", () => {
    const completed = ticket("completed-history", { stage: "done", agent_state: "idle" });
    const unfinished = ticket("unfinished-failure", { agent_state: "errored" });
    const rows = [completed, unfinished];
    const groups = workItemTicketGroups(rows);
    expect(groups.map((group) => [group.label, group.tickets.map((row) => row.id)]))
      .toEqual([["Errored", [unfinished.id]], ["Done", [completed.id]]]);
    expect(workItemTicketGroups(rows.map((row) => boardCard(row)))
      .map((group) => [group.label, group.tickets.map((row) => row.id)]))
      .toEqual([["Errored", [unfinished.id]], ["Done", [completed.id]]]);
    const rail = buildWorkspaceRail([], [boardItem(rows)]);
    expect(rail.items[0].groups.map((group) => [group.label, group.cards.map((row) => row.id)]))
      .toEqual([["Errored", [unfinished.id]]]);
    expect(dayVisualTicket(completed as unknown as DayTicket).group).toBe("done");

    expect(feedbackTicketStateLabel(completed)).toBe("Done");
    expect(feedbackTicketStateLabel(unfinished)).toBe("Errored");
    expect(conversationSignalReading(completed)).not.toContain("data-conversation-signal");
  });

  it.each([
    [{ awaiting_approval: true }, "awaiting_approval"],
    [{ awaiting_answer: true }, "awaiting_answer"],
    [{ assigned: true }, "assigned"],
    [{ awaiting_reply: true }, "awaiting_reply"]
  ] as const)("keeps %o above completed history and explicit failure", (values, group) => {
    for (const agent_state of ["idle", "errored"] as const) {
      const row = ticket("completed-attention", { stage: "done", agent_state, ...values });
      expect(workItemTicketGroupKey(row)).toBe(group);
      expect(conversationSignalReading(row)).not.toContain("data-conversation-signal");
    }
  });

  it("preserves live completed activity and explicit completed errors", () => {
    const live = ticket("completed-live", { stage: "done", agent_state: "working" });
    const failed = ticket("completed-claim", {
      stage: "done", ticket_status: "errored", agent_state: "errored"
    });
    expect(workItemTicketGroupKey(live)).toBe("agent");
    expect(conversationSignalReading(live)).not.toContain("data-conversation-signal");
    expect(workItemTicketGroupKey(failed)).toBe("errored");
  });

  it("uses the approved group order for overlaps", () => {
    const rows = [
      ticket("done", { stage: "done" }), ticket("blocked", { ticket_status: "blocked" }),
      ticket("empty"), ticket("agent-review", { awaiting_agent_approval: true }),
      ticket("closeout", { waiting_to_closeout: true }), ticket("working", { agent_state: "working" }),
      ticket("deferred", { is_backlogged: true }), ticket("failed", { agent_state: "errored" }),
      ticket("reply", { awaiting_reply: true }), ticket("assigned", { assigned: true }),
      ticket("answer", { awaiting_answer: true }), ticket("approval", { awaiting_approval: true })
    ];
    expect(workItemTicketGroups(rows).map(group => [group.key, group.tickets.map(row => row.id)])).toEqual([
      ["awaiting_approval", ["approval"]], ["awaiting_answer", ["answer"]], ["assigned", ["assigned"]],
      ["awaiting_reply", ["reply"]], ["errored", ["failed"]], ["backlog", ["deferred"]],
      ["agent", ["working"]], ["waiting_to_closeout", ["closeout"]],
      ["status_awaiting_approval", ["agent-review"]], ["empty", ["empty"]],
      ["blocked", ["blocked"]], ["done", ["done"]]
    ]);
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

  it("keeps ownership independent from canonical conversation marks", () => {
    const assignedAndWorking = ticket("assigned-working", {
      assigned: true,
      agent_state: "working"
    });
    expect(workItemTicketGroupKey(assignedAndWorking)).toBe("assigned");
    expect(conversationSignalReading(assignedAndWorking)).not.toContain("data-conversation-signal");
  });

  it("preserves canonical simultaneous work and message facts alongside attention", () => {
    const approval = ticket("approval", {
      conversation_signal:{working:true,message_for_owner:true},
      awaiting_approval: true,
      awaiting_reply: true,
      agent_state: "working"
    });
    const reply = ticket("reply", { awaiting_reply: true, agent_state: "working", conversation_signal:{working:true,message_for_owner:true} });
    const working = ticket("working", { assigned: true, agent_state: "working", conversation_signal:{working:true,message_for_owner:false} });
    for (const row of [approval, reply]) {
      const html = conversationSignalReading(row);
      expect(html).not.toContain('data-conversation-signal="working"');
      expect(html).toContain('data-conversation-signal="message"');
      expect(dayVisualTicket(row as unknown as DayTicket).ticket.conversation_signal).toEqual(row.conversation_signal);
    }
    expect(conversationSignalReading(working)).toContain('data-conversation-signal="working"');
    expect(conversationSignalReading(working)).not.toContain('data-conversation-signal="message"');
    const readReply = ticket(reply.id, { ...reply, awaiting_reply:false,
      conversation_signal:{ ...reply.conversation_signal!, message_for_owner:false } });
    expect(readReply.conversation_signal?.working).toBe(true);
    expect(conversationSignalReading(readReply)).toContain('data-conversation-signal="working"');
    expect(conversationSignalReading(readReply)).not.toContain('data-conversation-signal="message"');
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
      tickets: group.tickets.map((row) => ({ id: row.id, signal: row.conversation_signal }))
    }));
    expect(reading(fromWorkspace)).toEqual(reading(fromBoard));
    expect(fromWorkspace.find((group) => group.key === "assigned")?.tickets.map((row) => row.id))
      .toEqual(["newer", "older"]);
    expect(fromWorkspace.map((group) => group.label)).toEqual([
      "Yours",
      "Messages"
    ]);
  });

  it("uses complete board Item tickets even when Today has a different classification", () => {
    const today = ticket("same", { assigned: true, activity_at: 1 });
    const full = ticket("same", { awaiting_approval: true, activity_at: 2 });
    const offDay = ticket("off-day", { awaiting_approval: true });
    const summary = boardItem([full, offDay]);
    summary.ticket_rollup = { ...quiet, awaiting_reply: true };
    const rail = buildWorkspaceRail([boardCard(today)], [summary]);
    expect(rail.items[0].conversationSignal).toBeUndefined();
    expect(rail.items[0].groups[0].key).toBe("awaiting_approval");
    expect(rail.items[0].groups[0].cards.map((row) => row.id)).toEqual(["same", "off-day"]);
    expect(rail.groups[0].key).toBe("assigned");
    expect(buildWorkspaceRail([], []).items).toEqual([]);
  });

  it("keeps board membership and age, ID order with complete children", () => {
    const summaries = [
      { ...boardItem([]), id: "si_newer", created_at: 2 },
      { ...boardItem([ticket("done", { stage: "done" }), ticket("off-day", { assigned: true })]), id: "si_mirror", created_at: 1 },
      { ...boardItem([]), id: "si_oldest", created_at: 0 },
      { ...boardItem([]), id: "si_after", created_at: 1 }
    ];
    const rail = buildWorkspaceRail([], summaries);
    expect(rail.items.map((item) => item.id)).toEqual(["si_oldest", "si_after", "si_mirror", "si_newer"]);
    expect(rail.items.find((item) => item.id === "si_mirror")?.rested).toBe(false);
    expect(rail.items.find((item) => item.id === "si_mirror")?.groups[0].cards[0].id).toBe("off-day");
  });

});
