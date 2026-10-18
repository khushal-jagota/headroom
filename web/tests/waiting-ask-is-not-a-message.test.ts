/** What each screen says when a worker is waiting on an answer.
 *
 * A pending permission ask and an unread message were one fact. Every screen that read
 * it had one word for both, and the word it said was "message" — which a permission ask
 * is not. The dot said the same thing twice: `needs-me` is documented as the ask, and
 * the ask had no way to reach it on its own.
 *
 * These read what a screen says, not how it decides.
 */

import { describe, expect, it } from "vitest";

import { workItemTicketGroups } from "../src/lib/workItemPresentation";
import { conversationSignalReading } from "./support/conversationSignalReading";
import { dayActionTiles, dayPageState, dayVisualTicket } from "../src/lib/dayPresentation";
import { feedbackTicketStateLabel } from "../src/lib/feedback";
import { sprintTicketCondition } from "../src/lib/sprintPresentation";
import type { BoardCard, DayTicket } from "../src/lib/types";

function dayTicket(values: Partial<DayTicket>): DayTicket {
  return {
    id: "t_1",
    title: "A Ticket",
    stage: "needs_implementation",
    ticket_status: "agent",
    agent_state: "working",
    ...values
  } as DayTicket;
}

const quiet = {
  id: "t_1",
  title: "A Ticket",
  stage: "needs_implementation",
  ticket_status: "agent",
  awaiting_reply: false,
  awaiting_answer: false,
  awaiting_approval: false,
  awaiting_agent_approval: false,
  assigned: false,
  agent_state: "working"
} as const;

function boardCard(values: Partial<BoardCard>): BoardCard {
  return {
    id: "t_1",
    title: "A Ticket",
    deadline: null,
    activity_at: 0,
    has_pending_proposal: false,
    ticket_status: "agent",
    worker_type: "coding",
    employee_backend: "codex",
    stage: "needs_implementation",
    is_done: false,
    conversation_id: null,
    awaiting_reply: false,
    awaiting_answer: false,
    awaiting_approval: false,
    awaiting_agent_approval: false,
    assigned: false,
    agent_state: "working",
    ...values
  } as BoardCard;
}

describe("a worker waiting on an answer", () => {
  it("appears after approvals and before assignment and unread replies", () => {
    const groups = workItemTicketGroups([
      boardCard({ id: "reply", awaiting_reply: true }),
      boardCard({ id: "assigned", assigned: true, awaiting_reply: true }),
      boardCard({ id: "answer", awaiting_answer: true, assigned: true, awaiting_reply: true }),
      boardCard({ id: "approval", awaiting_approval: true, awaiting_answer: true })
    ]);
    expect(groups.map(({ label, tickets }) => [label, tickets.map(({ id }) => id)])).toEqual([
      ["Needs your approval", ["approval"]],
      ["Needs your answer", ["answer"]],
      ["Yours", ["assigned"]],
      ["Messages", ["reply"]]
    ]);
  });

  it("has no conversation dot on the Workspace rail", () => {
    const waiting = boardCard({ awaiting_answer: true });
    expect(conversationSignalReading(waiting)).not.toContain("data-conversation-signal");
  });

  it("is not called a message on the Sprint Item page", () => {
    expect(sprintTicketCondition({ ...quiet, awaiting_answer: true })).toEqual({
      word: "needs your answer"
    });
  });

  it("is not called a message in the Feedback inbox", () => {
    const waiting = { ...quiet, awaiting_answer: true };
    expect(feedbackTicketStateLabel(waiting)).toBe("Needs your answer");
    expect(conversationSignalReading(waiting)).not.toContain("data-conversation-signal");
  });
});

describe("an unread message", () => {
  it("is still a message on every screen, and keeps its own mark", () => {
    const withAMessage = boardCard({ awaiting_reply: true, conversation_signal: {working:false,message_for_owner:true} });
    expect(conversationSignalReading(withAMessage)).toContain('data-conversation-signal="message"');
    expect(sprintTicketCondition({ ...quiet, awaiting_reply: true }).word).toBe("messages");
    expect(feedbackTicketStateLabel({ ...quiet, awaiting_reply: true })).toBe("Messages");
  });

  it("keeps its message dot while the answer owns the group", () => {
    const both = boardCard({ awaiting_reply: true, awaiting_answer: true, conversation_signal: {working:false,message_for_owner:true} });
    expect(conversationSignalReading(both)).toContain('data-conversation-signal="message"');
  });

  it("still sits below a proposal the owner holds", () => {
    expect(
      sprintTicketCondition({ ...quiet, awaiting_answer: true, awaiting_approval: true }).word
    ).toBe("needs your approval");
  });
});

describe("the Day", () => {
  it("counts both a waiting answer and an unread message under Need you", () => {
    const tickets = [
      dayTicket({ id: "t_answer", awaiting_answer: true }),
      dayTicket({ id: "t_message", awaiting_reply: true }),
      dayTicket({ id: "t_quiet" })
    ].map(dayVisualTicket);

    const needYou = dayActionTiles(tickets).find((tile) => tile.key === "needs-me");
    expect(needYou?.count).toBe(2);
    expect(dayPageState(tickets)).toBe("populated");
  });

  it("gives only the unread message a dot", () => {
    const waiting = dayVisualTicket(dayTicket({ awaiting_answer:true }));
    const message = dayVisualTicket(dayTicket({ conversation_signal:{working:false,message_for_owner:true} }));
    expect(conversationSignalReading(waiting.ticket)).not.toContain("data-conversation-signal");
    expect(conversationSignalReading(message.ticket)).toContain('data-conversation-signal="message"');
  });
});
