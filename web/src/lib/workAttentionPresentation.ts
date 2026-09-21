import type { AgentState } from "./types";

export type WorkAttentionFacts = {
  awaiting_reply: boolean;
  awaiting_approval: boolean;
  assigned: boolean;
};

export type PrimaryWorkAttention = "awaiting_approval" | "assigned" | "awaiting_reply";

// A Ticket can carry more than one attention fact. Every screen classifies that
// overlap in the same order: approval first, then assignment, then reply.
export function primaryWorkAttention(
  attention: WorkAttentionFacts
): PrimaryWorkAttention | null {
  if (attention.awaiting_approval) return "awaiting_approval";
  if (attention.assigned) return "assigned";
  if (attention.awaiting_reply) return "awaiting_reply";
  return null;
}

export type AgentHoldFacts = {
  ticket_status?: string;
  agent_state?: AgentState;
};

// Whether a worker has this Ticket. Two facts answer it, and the durable one leads.
// `ticket_status` is `agent` from the moment the wakeup system sends a worker its step
// until the step comes back, and a worker that ends its turn to wait on a long job still
// holds it. A live turn can add the state; it must never be the only thing carrying it,
// or every screen reading it empties the moment the process stops.
//
// Every screen asks this question here. What outranks it is each screen's own business:
// the Workspace rail still leads with a broken worker, and approval still outranks
// assignment wherever both are true.
export function agentHoldsTicket(facts: AgentHoldFacts): boolean {
  return facts.ticket_status === "agent" || facts.agent_state === "working";
}
