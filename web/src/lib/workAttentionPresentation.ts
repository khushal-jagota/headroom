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
