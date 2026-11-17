/** Durable source of one continuation. No additional Ticket state. */
export interface WorkerContinuationSource {
  readonly ticket_id: string;
  readonly conversation_id: string;
  readonly claim_revision: number;
  readonly turn_number: number;
  readonly completed_sequence: number;
  readonly retry_sequence: number;
  readonly sender_message_id: string;
}
