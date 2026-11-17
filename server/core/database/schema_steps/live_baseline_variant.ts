/**
 * The one known variant of the Python baseline that the step runner accepts besides the
 * baseline itself. The live database, built by Alembic step by step, carries an older text of
 * the three signal_source_agents triggers: its WHERE clause keeps only `chief_of_staff` and
 * `manager_…` agent keys, so a `goal_…` agent row publishes no signal association. Step 1
 * (ts_0001_open_backend_keys) recreates them with the seed's text.
 *
 * Source: `SEED_STATEMENT_QUERY` on an isolated copy of the verified backup
 * snapshot-20261117T013058Z-f1c57ea0 (17 Nov 2026), recorded in the Ticket artifacts
 * database-live-schema-check.md and database-live-trigger-variant.json. The text is copied
 * exactly. Do not reformat it.
 *
 * To remove: delete this file, its one call in `applySchemaSteps` (core/database/index.ts), and
 * the variant's uses in schema_steps.test.ts.
 */

/** Trigger name to the exact live `sqlite_master.sql` text. */
export const LIVE_SIGNAL_SOURCE_AGENTS_TRIGGERS: Readonly<Record<string, string>> = Object.freeze({
  signal_source_agents_insert:
    "CREATE TRIGGER signal_source_agents_insert AFTER INSERT ON agents BEGIN INSERT INTO signal_publication_dirty SELECT NEW.conversation_id WHERE NEW.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=NEW.conversation_id);INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' ELSE 'manager' END,'owner_id',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' ELSE substr(NEW.agent_key,9) END,'relation','current','before_conversation_id',NULL,'after_conversation_id',NEW.conversation_id) WHERE 1 AND (NEW.agent_key='chief_of_staff' OR substr(NEW.agent_key,1,8)='manager_') AND NULL IS NOT NEW.conversation_id; END",
  signal_source_agents_update:
    "CREATE TRIGGER signal_source_agents_update BEFORE UPDATE ON agents BEGIN INSERT INTO signal_publication_dirty SELECT OLD.conversation_id WHERE OLD.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=OLD.conversation_id);INSERT INTO signal_publication_dirty SELECT NEW.conversation_id WHERE NEW.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=NEW.conversation_id);INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' ELSE 'manager' END,'owner_id',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' ELSE substr(NEW.agent_key,9) END,'relation','current','before_conversation_id',OLD.conversation_id,'after_conversation_id',NEW.conversation_id) WHERE OLD.agent_key IS NEW.agent_key AND (NEW.agent_key='chief_of_staff' OR substr(NEW.agent_key,1,8)='manager_') AND OLD.conversation_id IS NOT NEW.conversation_id;INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' ELSE 'manager' END,'owner_id',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' ELSE substr(OLD.agent_key,9) END,'relation','current','before_conversation_id',OLD.conversation_id,'after_conversation_id',NULL) WHERE OLD.agent_key IS NOT NEW.agent_key AND (OLD.agent_key='chief_of_staff' OR substr(OLD.agent_key,1,8)='manager_') AND OLD.conversation_id IS NOT NULL;INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' ELSE 'manager' END,'owner_id',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' ELSE substr(NEW.agent_key,9) END,'relation','current','before_conversation_id',NULL,'after_conversation_id',NEW.conversation_id) WHERE OLD.agent_key IS NOT NEW.agent_key AND (NEW.agent_key='chief_of_staff' OR substr(NEW.agent_key,1,8)='manager_') AND NULL IS NOT NEW.conversation_id; END",
  signal_source_agents_delete:
    "CREATE TRIGGER signal_source_agents_delete BEFORE DELETE ON agents BEGIN INSERT INTO signal_publication_dirty SELECT OLD.conversation_id WHERE OLD.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=OLD.conversation_id);INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' ELSE 'manager' END,'owner_id',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' ELSE substr(OLD.agent_key,9) END,'relation','current','before_conversation_id',OLD.conversation_id,'after_conversation_id',NULL) WHERE 1 AND (OLD.agent_key='chief_of_staff' OR substr(OLD.agent_key,1,8)='manager_') AND OLD.conversation_id IS NOT NULL; END",
});

/**
 * The baseline statements with all three triggers carrying the recorded live text, or null
 * when the baseline does not hold each of them exactly once.
 */
export function live_baseline_variant(baseline: readonly string[]): readonly string[] | null {
  const variant = [...baseline];
  for (const [name, live] of Object.entries(LIVE_SIGNAL_SOURCE_AGENTS_TRIGGERS)) {
    const positions = baseline.flatMap((statement, position) =>
      statement.startsWith(`CREATE TRIGGER ${name} `) ? [position] : [],
    );
    if (positions.length !== 1) return null;
    variant[positions[0]!] = live;
  }
  return variant;
}
