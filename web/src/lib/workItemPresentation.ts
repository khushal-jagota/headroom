import type { WorkAttention } from "./types";
import { agentHoldsTicket, primaryWorkAttention } from "./workAttentionPresentation";

export type WorkItemAttentionFacts = Omit<WorkAttention, 'awaiting_reply' | 'awaiting_answer' | 'agent_state'> & {
  awaiting_reply: boolean | null;
  awaiting_answer: boolean | null;
  agent_state: import('./types').AgentState | null;
};

export type WorkItemTicketFacts = WorkItemAttentionFacts & {
  conversation_signal?: Partial<import("./conversationSignal").ConversationSignal>;
  id: string;
  title: string;
  recap?: string;
  recap_preview?: string;
  blocked?: boolean;
  stage: string;
  worker_type?: string;
  conversation_id?: string | null;
  ticket_status: string;
  is_backlogged?: boolean;
  waiting_to_closeout?: boolean | null;
  gating_field?: string | null;
  activity_at?: number;
};

export type WorkItemTicketGroupKey =
  | "awaiting_approval"
  | "awaiting_answer"
  | "assigned"
  | "awaiting_reply"
  | "errored"
  | "backlog"
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
  { key: "backlog", label: "Backlog", quiet: false },
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

export type WorkItemTicketGroup<T extends WorkItemTicketFacts> =
  WorkItemTicketGroupDefinition & { tickets: T[] };

export function workItemTicketGroupKey(
  ticket: WorkItemTicketFacts
): WorkItemTicketGroupKey {
  if (ticket.is_backlogged) return "backlog";
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
  "awaiting_reply",
  "errored"
] as const;
