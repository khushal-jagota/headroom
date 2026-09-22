import type { FieldStageVisualState } from "./ui";
import type { AgentState, Priority, WorkAttention } from "./types";
import { agentHoldsTicket, primaryWorkAttention } from "./workAttentionPresentation";

export type WorkItemTicketFacts = WorkAttention & {
  id: string;
  title: string;
  priority: Priority;
  stage: string;
  ticket_status: string;
  waiting_to_closeout?: boolean;
  gating_field?: string | null;
  activity_at?: number;
};

export type WorkItemTicketGroupKey =
  | "awaiting_approval"
  | "awaiting_answer"
  | "assigned"
  | "awaiting_reply"
  | "errored"
  | "agent"
  | "waiting_to_closeout"
  | "status_awaiting_approval"
  | "empty"
  | "blocked"
  | "done";

export type WorkItemTicketGroupDefinition = {
  key: WorkItemTicketGroupKey;
  label: string;
  quiet: boolean;
};

export const WORK_ITEM_TICKET_GROUPS: readonly WorkItemTicketGroupDefinition[] = [
  { key: "awaiting_approval", label: "Needs your approval", quiet: false },
  { key: "awaiting_answer", label: "Needs your answer", quiet: false },
  { key: "assigned", label: "Yours", quiet: false },
  { key: "awaiting_reply", label: "Messages", quiet: false },
  { key: "errored", label: "Errored", quiet: false },
  { key: "agent", label: "Agent", quiet: false },
  { key: "waiting_to_closeout", label: "Waiting on Consequences", quiet: true },
  {
    key: "status_awaiting_approval",
    label: "Awaiting an agent's approval",
    quiet: true
  },
  { key: "empty", label: "Empty", quiet: true },
  { key: "blocked", label: "Blocked", quiet: true },
  { key: "done", label: "Done", quiet: true }
] as const;

export type WorkItemActivityMark =
  | "owner-approval"
  | "owner-answer"
  | "reply"
  | "working"
  | null;

export type WorkItemActivityMarkPresentation = {
  state: FieldStageVisualState;
  ariaLabel: string;
};

export type WorkItemTicketGroup<T extends WorkItemTicketFacts> =
  WorkItemTicketGroupDefinition & { tickets: T[] };

export function workItemTicketGroupKey(
  ticket: WorkItemTicketFacts
): WorkItemTicketGroupKey {
  const attention = primaryWorkAttention(ticket);
  if (attention !== null) return attention;
  if (ticket.agent_state === "errored") return "errored";
  if (agentHoldsTicket(ticket)) return "agent";
  if (ticket.waiting_to_closeout) return "waiting_to_closeout";
  if (ticket.awaiting_agent_approval) return "status_awaiting_approval";
  if (ticket.ticket_status === "blocked") return "blocked";
  if (ticket.stage === "done") return "done";
  return "empty";
}

export function workItemActivityMark(
  facts: Pick<
    WorkAttention,
    "awaiting_approval" | "awaiting_answer" | "awaiting_reply" | "agent_state"
  >
): WorkItemActivityMark {
  if (facts.awaiting_approval) return "owner-approval";
  if (facts.awaiting_answer) return "owner-answer";
  if (facts.awaiting_reply) return "reply";
  if (facts.agent_state === "working") return "working";
  return null;
}

export function workItemActivityMarkPresentation(
  mark: WorkItemActivityMark
): WorkItemActivityMarkPresentation {
  if (mark === "owner-approval") {
    return { state: "needs-me", ariaLabel: "Needs your approval" };
  }
  if (mark === "owner-answer") {
    return { state: "needs-me", ariaLabel: "Needs your answer" };
  }
  if (mark === "reply") {
    return { state: "current-awaiting-approval", ariaLabel: "Unread reply" };
  }
  if (mark === "working") {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  return { state: "upcoming", ariaLabel: "No current activity" };
}

export function workItemRollupActivityMark(
  own: WorkAttention,
  children: WorkAttention
): WorkItemActivityMark {
  return workItemActivityMark({
    awaiting_approval: own.awaiting_approval || children.awaiting_approval,
    awaiting_answer: own.awaiting_answer || children.awaiting_answer,
    awaiting_reply: own.awaiting_reply || children.awaiting_reply,
    agent_state: (
      own.agent_state === "working" || children.agent_state === "working"
        ? "working"
        : own.agent_state === "errored" || children.agent_state === "errored"
          ? "errored"
          : "idle"
    ) satisfies AgentState
  });
}

export function workItemTicketOrder(
  left: WorkItemTicketFacts,
  right: WorkItemTicketFacts
): number {
  return (
    (right.activity_at ?? 0) - (left.activity_at ?? 0) ||
    left.title.localeCompare(right.title, undefined, { sensitivity: "base" }) ||
    left.id.localeCompare(right.id)
  );
}

export function workItemTicketGroups<T extends WorkItemTicketFacts>(
  tickets: readonly T[],
  keys: readonly WorkItemTicketGroupKey[] = WORK_ITEM_TICKET_GROUPS.map((group) => group.key)
): WorkItemTicketGroup<T>[] {
  const definitions = new Map(WORK_ITEM_TICKET_GROUPS.map((group) => [group.key, group]));
  return keys.flatMap((key) => {
    const grouped = tickets
      .filter((ticket) => workItemTicketGroupKey(ticket) === key)
      .sort(workItemTicketOrder);
    const definition = definitions.get(key);
    return grouped.length && definition ? [{ ...definition, tickets: grouped }] : [];
  });
}

export const WORK_ITEM_ATTENTION_GROUP_KEYS: readonly WorkItemTicketGroupKey[] = [
  "awaiting_approval",
  "awaiting_answer",
  "assigned",
  "awaiting_reply"
] as const;
