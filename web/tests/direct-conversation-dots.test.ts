import { describe, expect, it } from "vitest";
import { conversationSignalPresentation } from "../src/lib/conversationSignalPresentation";
import { dayActionTiles, dayVisualTicket } from "../src/lib/dayPresentation";
import { feedbackTicketStageState } from "../src/lib/feedback";
import { sprintTicketCondition } from "../src/lib/sprintPresentation";
import { workItemActivityMark, workItemTicketGroupKey } from "../src/lib/workItemPresentation";
import { workspaceSprintItemRowMark } from "../src/lib/workspaceRail";
import type { DayTicket, WorkAttention } from "../src/lib/types";

const quiet: WorkAttention = {
  awaiting_reply: false, awaiting_answer: false, awaiting_approval: false,
  awaiting_agent_approval: false, assigned: false, agent_state: "idle"
};

// A dot is one conversation fact. Every combination of the independent work facts
// must keep the same message meaning, including a message alongside an approval.
const combinations = [false, true].flatMap((awaiting_reply) =>
  [false, true].flatMap((awaiting_approval) =>
    [false, true].flatMap((awaiting_answer) =>
      ["idle", "working", "errored"].map((agent_state) => ({
        ...quiet, awaiting_reply, awaiting_approval, awaiting_answer,
        agent_state: agent_state as WorkAttention["agent_state"]
      })))))
;

describe("conversation dots across Worker references", () => {
  it.each(combinations)("uses only awaiting_reply for %o", (facts) => {
    const ticket = {
      ...facts, id: "t_message", title: "Message", priority: "P1" as const,
      stage: "needs_implementation", ticket_status: "agent", conversation_id: null
    };
    const state = facts.awaiting_reply ? "current-awaiting-approval" : null;
    expect(conversationSignalPresentation(facts)?.state ?? null).toBe(state);
    expect(workItemActivityMark(facts)).toBe(facts.awaiting_reply ? "reply" : null);
    expect(sprintTicketCondition(ticket).mark).toBe(state);
    expect(feedbackTicketStageState(ticket)).toBe(state);
    expect(dayVisualTicket(ticket as DayTicket).state).toBe(state);
    expect(workItemTicketGroupKey(ticket)).toBe(
      facts.awaiting_approval ? "awaiting_approval" :
      facts.awaiting_answer ? "awaiting_answer" :
      facts.awaiting_reply ? "awaiting_reply" :
      facts.agent_state === "errored" ? "errored" : "agent"
    );
  });

  it("never rolls child messages, approval, questions, running, or error into a supervisor dot", () => {
    for (const child of combinations) {
      expect(workspaceSprintItemRowMark({ ...quiet, ticket_rollup: child } as WorkAttention)).toBeNull();
      expect(workspaceSprintItemRowMark({ ...quiet, awaiting_reply: true, ticket_rollup: child } as WorkAttention)).toBe("reply");
    }
  });

  it("keeps Home action counts when dots disappear or coexist with work", () => {
    const visual = (id: string, values: Partial<WorkAttention>) => dayVisualTicket({
      id, title: id, stage: "needs_implementation", ticket_status: "agent", conversation_id: null, ...quiet, ...values
    } as DayTicket);
    const tickets = [
      visual("approval-running", { awaiting_approval: true, agent_state: "working" }),
      visual("answer-running", { awaiting_answer: true, agent_state: "working" }),
      visual("assigned-running", { assigned: true, agent_state: "working" }),
      visual("assigned", { assigned: true }),
      visual("message", { awaiting_reply: true })
    ];
    expect(tickets.map((ticket) => ticket.state)).toEqual([null, null, null, null, "current-awaiting-approval"]);
    expect(dayActionTiles(tickets).map((tile) => [tile.key, tile.count])).toEqual([
      ["needs-me", 2], ["review", 1], ["working", 1], ["assigned", 1]
    ]);
  });
});
