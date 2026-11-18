/**
 * Step 1: backend keys become open strings. Five tables lose the CHECK that listed the
 * built-in backend kinds; every other constraint keeps its exact text. No column is added.
 *
 * Each table is rebuilt the standard SQLite way: create the new table, copy the rows, drop
 * the old table, rename the new one, then recreate its indexes and triggers. The runner turns
 * foreign keys off before the transaction, so a DROP neither cascades nor checks, and the
 * tables that reference a rebuilt one by name (backend_usage_windows, measure_sources) keep
 * that reference unchanged. The runner's foreign_key_check proves the rows still line up.
 *
 * The step also recreates the three signal_source_agents triggers with the seed's text. The
 * live database built by Python carries an older text whose WHERE clause keeps only
 * `chief_of_staff` and `manager_…` keys, so a `goal_…` agent row publishes no signal
 * association at all (artifacts/database-live-schema-check.md). The recreated triggers fire on
 * later changes only. Nothing is backfilled for an existing goal agent row: associations are
 * per-batch deltas that the producer deletes whenever it starts
 * (conversation/publication/coordinator.ts:135-136), their only reader marks navigation
 * conversations dirty (navigation/publication.ts:180-184), and the server writes agent rows
 * only for the Chief and Managers (runtime/conversation_start.ts:745, managers/data.ts:143).
 *
 * The step also takes over `navigation_view_ordered`, which the server used to create at run
 * time, so every used database holds it and no fresh one does. `IF NOT EXISTS` creates it on
 * a database without it and leaves an existing table and its rows untouched; SQLite stores the
 * same text either way (live_baseline_variant.ts). Its rows are a derived cache: the
 * navigation projection deletes and refills them from navigation_view_memberships.
 *
 * Every statement is a literal copied from the baseline seed, or for navigation_view_ordered
 * from the retired runtime statement. Never derive DDL at run time.
 */
import { PYTHON_BASELINE_SEED } from './baseline.ts';
import type { SchemaStep } from './contracts.ts';

export const open_backend_keys_step: SchemaStep = {
  revision: 'ts_0001_open_backend_keys',
  previous_revision: PYTHON_BASELINE_SEED.alembicRevision,
  apply(tx) {
    // backend_model_enablement
    tx.run(
      `CREATE TABLE backend_model_enablement_new (
          backend_key TEXT NOT NULL,
          model_id    TEXT NOT NULL,
          enabled     INTEGER NOT NULL CHECK (enabled IN (0,1)),
          PRIMARY KEY (backend_key, model_id)
        )`,
    );
    tx.run(
      'INSERT INTO backend_model_enablement_new (backend_key, model_id, enabled) SELECT backend_key, model_id, enabled FROM backend_model_enablement',
    );
    tx.run('DROP TABLE backend_model_enablement');
    tx.run('ALTER TABLE backend_model_enablement_new RENAME TO backend_model_enablement');
    tx.run(
      'CREATE TRIGGER "resource_model_enablement_delete" AFTER DELETE ON "backend_model_enablement" WHEN 1 BEGIN INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES (\'model-enablement\',OLD.backend_key || \':\' || OLD.model_id,1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END',
    );
    tx.run(
      'CREATE TRIGGER "resource_model_enablement_insert" AFTER INSERT ON "backend_model_enablement" WHEN 1 BEGIN INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES (\'model-enablement\',NEW.backend_key || \':\' || NEW.model_id,1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END',
    );
    tx.run(
      'CREATE TRIGGER "resource_model_enablement_update" AFTER UPDATE ON "backend_model_enablement" WHEN OLD.enabled IS NOT NEW.enabled BEGIN INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES (\'model-enablement\',NEW.backend_key || \':\' || NEW.model_id,1) ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END',
    );
    // backend_usage_snapshots
    tx.run(
      `CREATE TABLE backend_usage_snapshots_new (
          backend_key TEXT PRIMARY KEY,
          observed_at INTEGER NOT NULL
        )`,
    );
    tx.run(
      'INSERT INTO backend_usage_snapshots_new (backend_key, observed_at) SELECT backend_key, observed_at FROM backend_usage_snapshots',
    );
    tx.run('DROP TABLE backend_usage_snapshots');
    tx.run('ALTER TABLE backend_usage_snapshots_new RENAME TO backend_usage_snapshots');
    // goals
    tx.run(
      `CREATE TABLE goals_new (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
      title TEXT NOT NULL, why TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('binary','quantitative')),
      active INTEGER NOT NULL CHECK(active IN (0,1)), completed INTEGER NOT NULL CHECK(completed IN (0,1)),
      deadline TEXT, primary_chart TEXT CHECK(primary_chart IS NULL OR json_valid(primary_chart)),
      page TEXT NOT NULL CHECK(json_valid(page)), revision INTEGER NOT NULL CHECK(revision>0),
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      employee_backend TEXT NOT NULL,
      employee_launch_model TEXT NOT NULL, employee_launch_reasoning_effort TEXT, primary_checklist_block_id TEXT, completion_rule TEXT, completion_date TEXT, holder_kind TEXT NOT NULL DEFAULT 'chief' CHECK(holder_kind IN ('chief','manager')), holder_id TEXT REFERENCES managers(id) ON DELETE RESTRICT CHECK((holder_kind='chief' AND holder_id IS NULL) OR (holder_kind='manager' AND holder_id IS NOT NULL)))`,
    );
    tx.run(
      'INSERT INTO goals_new (id, project_id, title, why, kind, active, completed, deadline, primary_chart, page, revision, created_at, updated_at, employee_backend, employee_launch_model, employee_launch_reasoning_effort, primary_checklist_block_id, completion_rule, completion_date, holder_kind, holder_id) SELECT id, project_id, title, why, kind, active, completed, deadline, primary_chart, page, revision, created_at, updated_at, employee_backend, employee_launch_model, employee_launch_reasoning_effort, primary_checklist_block_id, completion_rule, completion_date, holder_kind, holder_id FROM goals',
    );
    tx.run('DROP TABLE goals');
    tx.run('ALTER TABLE goals_new RENAME TO goals');
    tx.run('CREATE INDEX goals_active ON goals(active,created_at,id)');
    tx.run('CREATE INDEX goals_holder ON goals(holder_kind,holder_id)');
    tx.run(
      "CREATE TRIGGER navigation_goals_delete BEFORE DELETE ON goals BEGIN INSERT INTO navigation_dirty(kind,id) SELECT 'goals',OLD.id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='goals' AND id=OLD.id); END",
    );
    tx.run(
      "CREATE TRIGGER navigation_goals_insert AFTER INSERT ON goals BEGIN INSERT INTO navigation_dirty(kind,id) SELECT 'goals',NEW.id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='goals' AND id=NEW.id); END",
    );
    tx.run(
      "CREATE TRIGGER navigation_goals_update BEFORE UPDATE ON goals BEGIN INSERT INTO navigation_dirty(kind,id) SELECT 'goals',OLD.id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='goals' AND id=OLD.id);INSERT INTO navigation_dirty(kind,id) SELECT 'goals',NEW.id WHERE NOT EXISTS(SELECT 1 FROM navigation_dirty WHERE kind='goals' AND id=NEW.id); END",
    );
    tx.run(
      `CREATE TRIGGER resource_goals_delete
        AFTER DELETE ON goals BEGIN
        INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES('goals','all',1)
        ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END`,
    );
    tx.run(
      `CREATE TRIGGER resource_goals_insert
        AFTER INSERT ON goals BEGIN
        INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES('goals','all',1)
        ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END`,
    );
    tx.run(
      `CREATE TRIGGER resource_goals_update
        AFTER UPDATE ON goals BEGIN
        INSERT INTO resource_source_versions(source_kind,source_id,revision) VALUES('goals','all',1)
        ON CONFLICT(source_kind,source_id) DO UPDATE SET revision=revision+1; END`,
    );
    // native_usage_files
    tx.run(
      `CREATE TABLE native_usage_files_new (
        provider TEXT NOT NULL,
        source_key TEXT NOT NULL,
        file_key TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        PRIMARY KEY(provider,source_key,file_key)
    )`,
    );
    tx.run(
      'INSERT INTO native_usage_files_new (provider, source_key, file_key, snapshot) SELECT provider, source_key, file_key, snapshot FROM native_usage_files',
    );
    tx.run('DROP TABLE native_usage_files');
    tx.run('ALTER TABLE native_usage_files_new RENAME TO native_usage_files');
    // native_usage_sources
    tx.run(
      `CREATE TABLE native_usage_sources_new (
        provider TEXT PRIMARY KEY,
        source_key TEXT NOT NULL,
        snapshot TEXT NOT NULL
    )`,
    );
    tx.run(
      'INSERT INTO native_usage_sources_new (provider, source_key, snapshot) SELECT provider, source_key, snapshot FROM native_usage_sources',
    );
    tx.run('DROP TABLE native_usage_sources');
    tx.run('ALTER TABLE native_usage_sources_new RENAME TO native_usage_sources');
    // navigation_view_ordered, kept as it is where the server already created it.
    tx.run(
      'CREATE TABLE IF NOT EXISTS navigation_view_ordered (section_key TEXT NOT NULL,profile TEXT NOT NULL,locale TEXT NOT NULL,position INTEGER NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,rank INTEGER NOT NULL,title_key BLOB NOT NULL,id_key BLOB NOT NULL,PRIMARY KEY(section_key,profile,locale,position),UNIQUE(section_key,profile,locale,kind,id))',
    );
    // The seed's signal_source_agents triggers, over either baseline variant.
    tx.run('DROP TRIGGER signal_source_agents_insert');
    tx.run(
      "CREATE TRIGGER signal_source_agents_insert AFTER INSERT ON agents BEGIN INSERT INTO signal_publication_dirty SELECT NEW.conversation_id WHERE NEW.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=NEW.conversation_id);INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' WHEN substr(NEW.agent_key,1,5)='goal_' THEN 'goal' ELSE 'manager' END,'owner_id',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' WHEN substr(NEW.agent_key,1,5)='goal_' THEN substr(NEW.agent_key,6) ELSE substr(NEW.agent_key,9) END,'relation','current','before_conversation_id',NULL,'after_conversation_id',NEW.conversation_id) WHERE 1 AND (NEW.agent_key='chief_of_staff' OR substr(NEW.agent_key,1,8)='manager_' OR substr(NEW.agent_key,1,5)='goal_') AND NULL IS NOT NEW.conversation_id; END",
    );
    tx.run('DROP TRIGGER signal_source_agents_update');
    tx.run(
      "CREATE TRIGGER signal_source_agents_update BEFORE UPDATE ON agents BEGIN INSERT INTO signal_publication_dirty SELECT OLD.conversation_id WHERE OLD.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=OLD.conversation_id);INSERT INTO signal_publication_dirty SELECT NEW.conversation_id WHERE NEW.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=NEW.conversation_id);INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' WHEN substr(NEW.agent_key,1,5)='goal_' THEN 'goal' ELSE 'manager' END,'owner_id',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' WHEN substr(NEW.agent_key,1,5)='goal_' THEN substr(NEW.agent_key,6) ELSE substr(NEW.agent_key,9) END,'relation','current','before_conversation_id',OLD.conversation_id,'after_conversation_id',NEW.conversation_id) WHERE OLD.agent_key IS NEW.agent_key AND (NEW.agent_key='chief_of_staff' OR substr(NEW.agent_key,1,8)='manager_' OR substr(NEW.agent_key,1,5)='goal_') AND OLD.conversation_id IS NOT NEW.conversation_id;INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' WHEN substr(OLD.agent_key,1,5)='goal_' THEN 'goal' ELSE 'manager' END,'owner_id',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' WHEN substr(OLD.agent_key,1,5)='goal_' THEN substr(OLD.agent_key,6) ELSE substr(OLD.agent_key,9) END,'relation','current','before_conversation_id',OLD.conversation_id,'after_conversation_id',NULL) WHERE OLD.agent_key IS NOT NEW.agent_key AND (OLD.agent_key='chief_of_staff' OR substr(OLD.agent_key,1,8)='manager_' OR substr(OLD.agent_key,1,5)='goal_') AND OLD.conversation_id IS NOT NULL;INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' WHEN substr(NEW.agent_key,1,5)='goal_' THEN 'goal' ELSE 'manager' END,'owner_id',CASE WHEN NEW.agent_key='chief_of_staff' THEN 'chief' WHEN substr(NEW.agent_key,1,5)='goal_' THEN substr(NEW.agent_key,6) ELSE substr(NEW.agent_key,9) END,'relation','current','before_conversation_id',NULL,'after_conversation_id',NEW.conversation_id) WHERE OLD.agent_key IS NOT NEW.agent_key AND (NEW.agent_key='chief_of_staff' OR substr(NEW.agent_key,1,8)='manager_' OR substr(NEW.agent_key,1,5)='goal_') AND NULL IS NOT NEW.conversation_id; END",
    );
    tx.run('DROP TRIGGER signal_source_agents_delete');
    tx.run(
      "CREATE TRIGGER signal_source_agents_delete BEFORE DELETE ON agents BEGIN INSERT INTO signal_publication_dirty SELECT OLD.conversation_id WHERE OLD.conversation_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM signal_publication_dirty WHERE conversation_id=OLD.conversation_id);INSERT INTO signal_publication_associations(value) SELECT json_object('owner_kind',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' WHEN substr(OLD.agent_key,1,5)='goal_' THEN 'goal' ELSE 'manager' END,'owner_id',CASE WHEN OLD.agent_key='chief_of_staff' THEN 'chief' WHEN substr(OLD.agent_key,1,5)='goal_' THEN substr(OLD.agent_key,6) ELSE substr(OLD.agent_key,9) END,'relation','current','before_conversation_id',OLD.conversation_id,'after_conversation_id',NULL) WHERE 1 AND (OLD.agent_key='chief_of_staff' OR substr(OLD.agent_key,1,8)='manager_' OR substr(OLD.agent_key,1,5)='goal_') AND OLD.conversation_id IS NOT NULL; END",
    );
  },
};
