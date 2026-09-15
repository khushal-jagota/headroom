import { describe, expect, it } from "vitest";

import { dayVisualTicket } from "../src/lib/dayPresentation";
import {
  feedbackTicketStageState,
  feedbackTicketStateLabel,
  type FeedbackTicket
} from "../src/lib/feedback";
import { sprintTicketCondition, type TicketConditionFacts } from "../src/lib/sprintPresentation";
import { ticketStatusGroupKey } from "../src/lib/ticketStatusGroups";
import type { DayTicket } from "../src/lib/types";
import { primaryWorkAttention } from "../src/lib/workAttentionPresentation";
import { workspaceCardGroupKey } from "../src/lib/workspaceRail";
import { boardCard } from "./boardCardFixture";

const attention = {
  awaiting_approval: true,
  assigned: true,
  awaiting_reply: true
};

const ticketFacts: TicketConditionFacts = {
  stage: "needs_implementation",
  ticket_status: "empty",
  waiting_to_closeout: false,
  agent_state: "idle",
  ...attention
};

describe("shared work-attention precedence", () => {
  it("selects approval before assignment, and assignment before reply", () => {
    expect(primaryWorkAttention(attention)).toBe("awaiting_approval");
    expect(primaryWorkAttention({ ...attention, awaiting_approval: false })).toBe("assigned");
    expect(
      primaryWorkAttention({ ...attention, awaiting_approval: false, assigned: false })
    ).toBe("awaiting_reply");
  });

  it("classifies overlapping facts the same on every Ticket grouping surface", () => {
    const feedbackTicket: FeedbackTicket = {
      id: "t_attention",
      title: "Attention",
      ...ticketFacts
    };
    const dayTicket: DayTicket = {
      id: "t_attention",
      title: "Attention",
      stage: ticketFacts.stage,
      ticket_status: ticketFacts.ticket_status,
      conversation_id: null,
      gating_field: "implementation",
      ...attention,
      agent_state: "idle"
    };

    expect(sprintTicketCondition(ticketFacts).mark).toBe("current-awaiting-approval");
    expect(ticketStatusGroupKey(ticketFacts)).toBe("current-awaiting-approval");
    expect(workspaceCardGroupKey(boardCard("t_attention", attention))).toBe(
      "awaiting_approval"
    );
    expect(dayVisualTicket(dayTicket)).toMatchObject({
      group: "awaiting_approval",
      state: "current-awaiting-approval"
    });
    expect(feedbackTicketStageState(feedbackTicket)).toBe("current-awaiting-approval");
    expect(feedbackTicketStateLabel(feedbackTicket)).toBe("Awaiting approval");
  });

  it("keeps assignment above an unread reply on every Ticket grouping surface", () => {
    const assignedAndReply = { ...attention, awaiting_approval: false };
    const facts = { ...ticketFacts, ...assignedAndReply };
    const feedbackTicket: FeedbackTicket = {
      id: "t_attention",
      title: "Attention",
      ...facts
    };
    const dayTicket: DayTicket = {
      id: "t_attention",
      title: "Attention",
      stage: facts.stage,
      ticket_status: facts.ticket_status,
      conversation_id: null,
      ...assignedAndReply,
      agent_state: "idle"
    };

    expect(sprintTicketCondition(facts).mark).toBe("current-paired");
    expect(ticketStatusGroupKey(facts)).toBe("current-paired");
    expect(workspaceCardGroupKey(boardCard("t_attention", assignedAndReply))).toBe("assigned");
    expect(dayVisualTicket(dayTicket)).toMatchObject({
      group: "assigned",
      state: "current-paired"
    });
    expect(feedbackTicketStageState(feedbackTicket)).toBe("current-paired");
    expect(feedbackTicketStateLabel(feedbackTicket)).toBe("Assigned");
  });
});
