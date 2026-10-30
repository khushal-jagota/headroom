import { describe, expect, it } from "vitest";
import { workItemTicketGroupKey } from "../src/lib/workItemPresentation";
import { buildWorkspaceRail } from "../src/lib/workspaceRail";
import type { BoardManager } from "../src/lib/types";
import type { WorkAttention } from "../src/lib/types";

const quiet: WorkAttention = {
  awaiting_reply: false, awaiting_answer: false, awaiting_approval: false,
  awaiting_agent_approval: false, assigned: false, agent_state: "idle"
};

describe("conversation dots across Worker references", () => {
  it.each([
    [{ awaiting_approval: true, awaiting_answer: true, awaiting_reply: true, agent_state: "errored" }, "awaiting_approval"],
    [{ awaiting_answer: true, awaiting_reply: true, assigned: true }, "awaiting_answer"],
    [{ assigned: true, awaiting_reply: true }, "assigned"],
    [{ awaiting_reply: true }, "awaiting_reply"],
    [{ agent_state: "errored" }, "errored"],
    [{}, "agent"]
  ] as const)("classifies literal work facts without inventing a conversation signal: %j", (facts, expected) => {
    const ticket = {
      ...quiet, ...facts, id: "literal-worker", title: "Literal Worker",
      stage: "needs_implementation", ticket_status: "agent", conversation_id: null
    };
    expect(workItemTicketGroupKey(ticket)).toBe(expected);
    expect(buildWorkspaceRail([], [{ id: "item", title: "Item", tickets: [ticket], ...quiet, ticket_rollup: quiet } as BoardManager]).items[0].groups[0].cards[0].conversation_signal).toBeUndefined();
  });

  it("keeps the manager canonical signal separate from children and legacy attention", () => {
    const own = { working:true, message_for_owner:true };
    const item = {id:"item",title:"Item",recap:"",recap_preview:"",created_at:1,updated_at:1,tickets:[],conversation_id:"own",
      ...quiet, awaiting_reply:false, conversation_signal:own,ticket_rollup:{...quiet,awaiting_reply:true}} as BoardManager;
    expect(buildWorkspaceRail([], [item]).items[0].conversationSignal).toEqual(own);
    delete item.conversation_signal;
    expect(buildWorkspaceRail([], [item]).items[0].conversationSignal).toBeUndefined();
  });

});
