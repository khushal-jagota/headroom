import type { SchemaStep } from './contracts.ts';

export const recipient_deliveries_step: SchemaStep = {
  revision: 'ts_0017_recipient_deliveries',
  previous_revision: 'ts_0016_agent_message_acceptances',
  apply(tx) {
    tx.run(`CREATE TABLE recipient_lifecycles (
      recipient_kind TEXT NOT NULL,
      recipient_id TEXT NOT NULL,
      generation INTEGER NOT NULL,
      retiring INTEGER NOT NULL CHECK(retiring IN (0,1)),
      PRIMARY KEY(recipient_kind,recipient_id)
    )`);
    tx.run(`CREATE TABLE recipient_deliveries (
      delivery_order INTEGER PRIMARY KEY AUTOINCREMENT,
      submission_id TEXT NOT NULL UNIQUE,
      recipient_kind TEXT NOT NULL,
      recipient_id TEXT NOT NULL,
      conversation_id TEXT NOT NULL,
      generation TEXT NOT NULL,
      payload TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('pending','held','dispatching','delivered','refused','uncertain','discarded')),
      attempt_id TEXT NOT NULL,
      native_transport TEXT CHECK(native_transport IN ('local','mailbox')),
      outcome TEXT,
      settled_at INTEGER
    )`);
    tx.run(
      'CREATE INDEX recipient_delivery_pending ON recipient_deliveries(recipient_kind,recipient_id,state,delivery_order)',
    );
    tx.run(`CREATE TABLE recipient_delivery_commands (
      command_id TEXT NOT NULL REFERENCES executor_commands(command_id),
      submission_id TEXT NOT NULL REFERENCES recipient_deliveries(submission_id) ON DELETE CASCADE,
      attempt_id TEXT NOT NULL,
      PRIMARY KEY(command_id,submission_id)
    )`);
    tx.run(
      'CREATE INDEX recipient_delivery_attempt_commands ON recipient_delivery_commands(submission_id,attempt_id)',
    );
    tx.run('CREATE INDEX recipient_delivery_retention ON recipient_deliveries(state,settled_at)');
    tx.run(`CREATE TABLE conversation_held_prompts (
      held_prompt_id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
      position INTEGER NOT NULL,
      payload TEXT NOT NULL,
      dispatching INTEGER NOT NULL DEFAULT 0 CHECK(dispatching IN (0,1)),
      dispatch_turn INTEGER
    )`);
    tx.run(
      'CREATE INDEX conversation_held_order ON conversation_held_prompts(conversation_id,position)',
    );
  },
};
