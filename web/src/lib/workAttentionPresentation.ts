export type WorkAttentionFacts = {
  awaiting_reply: boolean;
  awaiting_answer?: boolean;
  awaiting_approval: boolean;
  assigned: boolean;
};

export type PrimaryWorkAttention =
  | "awaiting_approval"
  | "awaiting_answer"
  | "assigned"
  | "awaiting_reply";

// A Ticket can carry more than one attention fact. Every screen classifies that overlap
// in the same order: approval first, then a waiting ask, then assignment, then reply.
//
// Approval stays first because that is the one thing the owner asked to see above
// everything else at rest. The ask sits next because a decision he owes and an answer he
// owes both stop the work dead, and a Stage that is merely his to do does not.
export function primaryWorkAttention(
  attention: WorkAttentionFacts
): PrimaryWorkAttention | null {
  if (attention.awaiting_approval) return "awaiting_approval";
  if (attention.awaiting_answer) return "awaiting_answer";
  if (attention.assigned) return "assigned";
  if (attention.awaiting_reply) return "awaiting_reply";
  return null;
}
