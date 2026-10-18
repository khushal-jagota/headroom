import { describe, expect, it } from "vitest";
import { conversationSignalReading } from "./support/conversationSignalReading";
import { dayActionTiles, dayVisualTicket } from "../src/lib/dayPresentation";
import { workItemTicketGroupKey } from "../src/lib/workItemPresentation";
import { buildWorkspaceRail } from "../src/lib/workspaceRail";
import type { BoardSprintItem } from "../src/lib/types";
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
  it.each(combinations)("does not infer marks from legacy attention %o", (facts) => {
    const ticket = {
      ...facts, id: "t_message", title: "Message", stage: "needs_implementation", ticket_status: "agent", conversation_id: null
    };
    expect(conversationSignalReading(ticket)).not.toContain("data-conversation-signal");
    expect(dayVisualTicket(ticket as DayTicket).ticket.conversation_signal).toBeUndefined();
    expect(workItemTicketGroupKey(ticket)).toBe(
      facts.awaiting_approval ? "awaiting_approval" :
      facts.awaiting_answer ? "awaiting_answer" :
      facts.awaiting_reply ? "awaiting_reply" :
      facts.agent_state === "errored" ? "errored" : "agent"
    );
  });

  it("keeps the supervisor canonical signal separate from children and legacy attention", () => {
    const own = { working:true, message_for_owner:true };
    const item = {id:"item",title:"Item",recap_preview:"",created_at:1,tickets:[],conversation_id:"own",
      ...quiet, awaiting_reply:false, conversation_signal:own,ticket_rollup:{...quiet,awaiting_reply:true}} as BoardSprintItem;
    expect(buildWorkspaceRail([], [item]).items[0].conversationSignal).toEqual(own);
    delete item.conversation_signal;
    expect(buildWorkspaceRail([], [item]).items[0].conversationSignal).toBeUndefined();
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
    expect(tickets.every((ticket) => !ticket.ticket.conversation_signal)).toBe(true);
    expect(dayActionTiles(tickets).map((tile) => [tile.key, tile.count])).toEqual([
      ["needs-me", 2], ["review", 1], ["working", 1], ["assigned", 1]
    ]);
  });
});
