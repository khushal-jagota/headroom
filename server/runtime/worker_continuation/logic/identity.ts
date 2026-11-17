import type { WorkerContinuationSource } from '../contracts.ts';
export const CONTINUATION_PREFIX = 'worker_step_continuation:';
export const CONTINUATION_PROMPT =
  'Your previous turn ended without a proposal for the current Worker step. ' +
  'Read your Ticket with panels worker my-ticket and continue its current stage. ' +
  'Check prior work and results before you repeat any action. ' +
  'Complete the authorized work and submit the required proposal. ' +
  'Respect blockers, pending decisions, and explicit stops.';
export function continuation_identity(
  source: Omit<WorkerContinuationSource, 'sender_message_id' | 'conversation_id'>,
): string {
  return `${CONTINUATION_PREFIX}${source.ticket_id}:${source.claim_revision}:${source.turn_number}:${source.completed_sequence}:${source.retry_sequence}`;
}
export function parse_continuation_identity(
  identity: string | null,
): Omit<WorkerContinuationSource, 'conversation_id'> | null {
  if (identity === null || !identity.startsWith(CONTINUATION_PREFIX)) return null;
  const parts = identity.slice(CONTINUATION_PREFIX.length).split(':');
  if (parts.length !== 5 || !parts[0] || parts.slice(1).some((part) => !/^\d+$/.test(part)))
    return null;
  return {
    ticket_id: parts[0],
    claim_revision: Number(parts[1]),
    turn_number: Number(parts[2]),
    completed_sequence: Number(parts[3]),
    retry_sequence: Number(parts[4]),
    sender_message_id: identity,
  };
}
