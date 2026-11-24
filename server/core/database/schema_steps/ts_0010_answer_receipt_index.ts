/**
 * An answer's receipt is looked up by its conversation and operation ID on every answer that
 * carries one. The trailing sequence serves the lookup's newest-first order.
 */
import type { SchemaStep } from './contracts.ts';
export const answer_receipt_index_step: SchemaStep = {
  revision: 'ts_0010_answer_receipt_index',
  previous_revision: 'ts_0009_work_moves',
  apply(tx) {
    tx.run(
      "CREATE INDEX idx_conversation_events_answer_operation_id ON conversation_events(conversation_id,json_extract(payload,'$.operation_id'),sequence) WHERE kind IN ('permission_answered','user_input_answered')",
    );
  },
};
