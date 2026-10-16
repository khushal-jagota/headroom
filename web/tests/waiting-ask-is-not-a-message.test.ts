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

import { conversationSignalPresentation } from "../src/lib/conversationSignalPresentation";
import { dayActionTiles, dayPageState, dayVisualTicket } from "../src/lib/dayPresentation";
import { feedbackTicketStageState, feedbackTicketStateLabel } from "../src/lib/feedback";
import { sprintTicketCondition } from "../src/lib/sprintPresentation";
import {
  workspaceRowMarkPresentation,
  workspaceTicketRowMark
} from "../src/lib/workspaceRail";
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
    priority: "P1",
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
  it("has no conversation dot on the Workspace rail", () => {
    const waiting = boardCard({ awaiting_answer: true });
    const mark = workspaceTicketRowMark(waiting);
    expect(workspaceRowMarkPresentation(mark)).toEqual({
      state: null,
      ariaLabel: ""
    });
  });

  it("is not called a message on the Sprint Item page", () => {
    expect(sprintTicketCondition({ ...quiet, awaiting_answer: true })).toEqual({
      mark: null,
      word: "needs your answer"
    });
  });

  it("is not called a message in the Feedback inbox", () => {
    const waiting = { ...quiet, awaiting_answer: true };
    expect(feedbackTicketStateLabel(waiting)).toBe("Needs your answer");
    expect(feedbackTicketStageState(waiting)).toBeNull();
  });

  it("is not called a message on the Agents roster", () => {
    expect(
      conversationSignalPresentation({
        awaiting_reply: false,
        awaiting_answer: true,
        agent_state: "working"
      })
    ).toBeNull();
  });
});

describe("an unread message", () => {
  it("is still a message on every screen, and keeps its own mark", () => {
    const withAMessage = boardCard({ awaiting_reply: true });
    expect(workspaceRowMarkPresentation(workspaceTicketRowMark(withAMessage))).toEqual({
      state: "current-awaiting-approval",
      ariaLabel: "Unread reply"
    });
    expect(sprintTicketCondition({ ...quiet, awaiting_reply: true }).word).toBe("messages");
    expect(feedbackTicketStateLabel({ ...quiet, awaiting_reply: true })).toBe("Messages");
  });

  it("keeps its message dot while the answer owns the group", () => {
    const both = boardCard({ awaiting_reply: true, awaiting_answer: true });
    expect(workspaceRowMarkPresentation(workspaceTicketRowMark(both)).ariaLabel).toBe(
      "Unread reply"
    );
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
    expect(dayVisualTicket(dayTicket({ awaiting_answer: true })).state).toBeNull();
    expect(dayVisualTicket(dayTicket({ awaiting_reply: true })).state).toBe(
      "current-awaiting-approval"
    );
  });
});
