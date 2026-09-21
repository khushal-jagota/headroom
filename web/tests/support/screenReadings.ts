// One Ticket, read by every screen that says whose it is and whether a worker has it.
//
// Each helper returns the words or the mark a reader actually sees, so a test here
// fails when a screen changes what it says — not when a function is renamed.
import { dayActionTiles, dayVisualTicket } from "../../src/lib/dayPresentation";
import { feedbackTicketStageState, feedbackTicketStateLabel } from "../../src/lib/feedback";
import {
  buildLifecycle,
  fieldStageVisualStateFor,
  gatingFieldFor,
  type WorkerTypeManifest
} from "../../src/lib/lifecycle";
import { sprintTicketCondition } from "../../src/lib/sprintPresentation";
import {
  TICKET_STATUS_GROUPS,
  ticketStatusGroupKey
} from "../../src/lib/ticketStatusGroups";
import type { AgentState, BoardCard, DayTicket, Principal, TicketDetail } from "../../src/lib/types";
import type { FieldStageVisualState } from "../../src/lib/ui";
import { workspaceGroups } from "../../src/lib/workspaceRail";

export type TicketFacts = {
  stage: string;
  ticket_status: string;
  gating_field: string;
  awaiting_reply: boolean;
  awaiting_approval: boolean;
  awaiting_agent_approval: boolean;
  assigned: boolean;
  agent_state: AgentState;
  waiting_to_closeout: boolean;
  is_done: boolean;
};

export function ticketFacts(overrides: Partial<TicketFacts> = {}): TicketFacts {
  return {
    stage: "needs_brief",
    ticket_status: "empty",
    gating_field: "brief",
    awaiting_reply: false,
    awaiting_approval: false,
    awaiting_agent_approval: false,
    assigned: false,
    agent_state: "idle",
    waiting_to_closeout: false,
    is_done: false,
    ...overrides
  };
}

// A Worker type whose Brief belongs to the worker, which is how thirteen of the
// fourteen shipped types declare it.
const MANIFEST: WorkerTypeManifest = {
  worker_type: "coding",
  label: "Coding",
  stages: [
    {
      id: "needs_brief",
      label: "Brief",
      gating_field: "brief",
      is_terminal: false,
      ownership_mode: "worker"
    },
    {
      id: "needs_plan",
      label: "Plan",
      gating_field: "plan",
      is_terminal: false,
      ownership_mode: "worker"
    },
    { id: "done", label: "Done", gating_field: null, is_terminal: true, ownership_mode: null }
  ],
  advance: { needs_brief: "needs_plan", needs_plan: "done" },
  fields: [
    { id: "brief", label: "Brief" },
    { id: "plan", label: "Plan" }
  ],
  ceiling_range: ["needs_brief", "needs_plan"],
  default_ceiling: "needs_brief",
  worker_profile_id: "panels-worker-coding",
  default_backend: "claude",
  default_model: null,
  default_reasoning_effort: null
};

const OWNER: Principal = { kind: "owner", id: "owner" };

function boardCard(facts: TicketFacts): BoardCard {
  return {
    id: "t_reading",
    title: "One Ticket",
    priority: "P1",
    deadline: null,
    project_id: null,
    project: null,
    group_project_id: null,
    group_project: null,
    activity_at: 1,
    has_pending_proposal: facts.ticket_status === "awaiting_approval",
    ticket_status: facts.ticket_status,
    worker_type: "coding",
    employee_backend: "claude",
    stage: facts.stage,
    stage_label: "Brief",
    gating_field: facts.gating_field,
    gating_field_label: "Brief",
    is_done: facts.is_done,
    blocked: facts.ticket_status === "blocked",
    conversation_id: "c_reading",
    waiting_to_closeout: facts.waiting_to_closeout,
    sprint_item_id: null,
    sprint_item_title: null,
    sprint_item_priority: null,
    awaiting_reply: facts.awaiting_reply,
    awaiting_approval: facts.awaiting_approval,
    awaiting_agent_approval: facts.awaiting_agent_approval,
    assigned: facts.assigned,
    agent_state: facts.agent_state
  };
}

function dayTicket(facts: TicketFacts): DayTicket {
  return {
    id: "t_reading",
    title: "One Ticket",
    stage: facts.stage,
    ticket_status: facts.ticket_status,
    conversation_id: "c_reading",
    is_done: facts.is_done,
    waiting_to_closeout: facts.waiting_to_closeout,
    gating_field: facts.gating_field,
    awaiting_reply: facts.awaiting_reply,
    awaiting_approval: facts.awaiting_approval,
    awaiting_agent_approval: facts.awaiting_agent_approval,
    assigned: facts.assigned,
    agent_state: facts.agent_state
  };
}

function ticketDetail(facts: TicketFacts): TicketDetail {
  return {
    id: "t_reading",
    title: "One Ticket",
    worker_type: "coding",
    employee_backend: "claude",
    employee_launch_model: null,
    employee_launch_reasoning_effort: null,
    employee_configuration_editable: true,
    stage: facts.stage,
    ceiling: "needs_plan",
    ceiling_holder: OWNER,
    priority: "P1",
    resolved_priority_anchors: { project: null, sprint_item: null },
    ticket_status: facts.ticket_status,
    conversation_id: "c_reading",
    conversation_history: [],
    guidance: "",
    field_values: {},
    pending_proposal: null,
    awaiting_reply: facts.awaiting_reply,
    awaiting_approval: facts.awaiting_approval,
    awaiting_agent_approval: facts.awaiting_agent_approval,
    assigned: facts.assigned,
    agent_state: facts.agent_state
  };
}

/** The heading the Ticket sits under on the Workspace rail. */
export function workspaceHeading(facts: TicketFacts): string {
  return workspaceGroups([boardCard(facts)])[0].label;
}

/** The word on the Ticket's row on the Sprint Item page. */
export function sprintItemWord(facts: TicketFacts): string {
  return sprintTicketCondition(facts).word;
}

/** The heading the Ticket sits under on the Sprint Item page. */
export function sprintItemHeading(facts: TicketFacts): string {
  const key = ticketStatusGroupKey(facts);
  return TICKET_STATUS_GROUPS.find((group) => group.key === key)?.label ?? key;
}

/** The state label beside the Ticket in the Feedback inbox. */
export function feedbackLabel(facts: TicketFacts): string {
  return feedbackTicketStateLabel({
    id: "t_reading",
    title: "One Ticket",
    stage: facts.stage,
    ticket_status: facts.ticket_status,
    awaiting_reply: facts.awaiting_reply,
    awaiting_approval: facts.awaiting_approval,
    awaiting_agent_approval: facts.awaiting_agent_approval,
    assigned: facts.assigned,
    agent_state: facts.agent_state
  });
}

export function feedbackMark(facts: TicketFacts): FieldStageVisualState {
  return feedbackTicketStageState({
    id: "t_reading",
    title: "One Ticket",
    stage: facts.stage,
    ticket_status: facts.ticket_status,
    awaiting_reply: facts.awaiting_reply,
    awaiting_approval: facts.awaiting_approval,
    awaiting_agent_approval: facts.awaiting_agent_approval,
    assigned: facts.assigned,
    agent_state: facts.agent_state
  });
}

/** The tiles Home shows for a Day holding this one Ticket, as "Label count". */
export function homeTiles(facts: TicketFacts): string[] {
  return dayActionTiles([dayVisualTicket(dayTicket(facts))]).map(
    (tile) => `${tile.label} ${tile.count}`
  );
}

/** What Home's dot for this Ticket is announced as. */
export function homeDotLabel(facts: TicketFacts): string {
  return dayVisualTicket(dayTicket(facts)).ariaLabel;
}

/** The mark on the Ticket's own page, against the field its Stage is gated on. */
export function ticketPageMark(facts: TicketFacts): FieldStageVisualState {
  const lifecycle = buildLifecycle(MANIFEST);
  const field = gatingFieldFor(lifecycle, facts.stage) ?? facts.gating_field;
  return fieldStageVisualStateFor(lifecycle, ticketDetail(facts), field);
}
