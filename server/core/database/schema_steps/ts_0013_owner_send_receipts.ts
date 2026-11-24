import type { SchemaStep } from './contracts.ts';

export const owner_send_receipts_step: SchemaStep = {
  revision: 'ts_0013_owner_send_receipts',
  previous_revision: 'ts_0012_empty_attachments',
  apply(tx) {
    tx.run(`CREATE TABLE owner_send_receipts (
      owner_kind TEXT NOT NULL,
      owner_id TEXT NOT NULL,
      message_id TEXT NOT NULL,
      body_digest TEXT NOT NULL,
      conversation_id TEXT,
      epoch TEXT,
      response TEXT,
      pending_kind TEXT,
      pending_payload TEXT,
      PRIMARY KEY(owner_kind, owner_id, message_id),
      UNIQUE(message_id)
    )`);
    tx.run(
      'CREATE INDEX owner_send_unsettled ON owner_send_receipts(epoch) WHERE pending_kind IS NOT NULL',
    );
    tx.run(`CREATE INDEX conversation_owner_sender_message ON conversation_events(json_extract(payload,'$.sender_message_id'),conversation_id,sequence)
      WHERE kind IN ('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded')`);
  },
};
