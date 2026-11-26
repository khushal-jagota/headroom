import type { SchemaStep } from './contracts.ts';

export const agent_message_acceptances_step: SchemaStep = {
  revision: 'ts_0016_agent_message_acceptances',
  previous_revision: 'ts_0015_owner_time_zone',
  apply(tx) {
    tx.run(`CREATE TABLE agent_message_acceptances (
      submission_id TEXT PRIMARY KEY,
      machine_id TEXT NOT NULL,
      sender_id TEXT NOT NULL,
      source_conversation_id TEXT NOT NULL,
      recipient_kind TEXT NOT NULL,
      recipient_id TEXT NOT NULL,
      recipient_order INTEGER NOT NULL,
      pending INTEGER NOT NULL CHECK(pending IN (0,1)),
      retained_bytes INTEGER NOT NULL,
      record_json TEXT NOT NULL
    )`);
    tx.run(
      'CREATE INDEX agent_message_pending ON agent_message_acceptances(recipient_kind,recipient_id,pending,recipient_order)',
    );
    tx.run(`CREATE TABLE agent_message_files (
      submission_id TEXT NOT NULL REFERENCES agent_message_acceptances(submission_id),
      file_index INTEGER NOT NULL,
      manifest_json TEXT NOT NULL,
      bytes BLOB NOT NULL,
      PRIMARY KEY(submission_id,file_index)
    )`);
  },
};
