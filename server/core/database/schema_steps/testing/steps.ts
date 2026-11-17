/** A test-only chain on top of the Python baseline. Releases never ship this folder. */
import { PYTHON_BASELINE_SEED } from '../baseline.ts';
import type { SchemaStep } from '../contracts.ts';

export const BASELINE_SEED = PYTHON_BASELINE_SEED;

/** A probe table with a CHECK, a child that references it, and rows in both. */
export const probe_step: SchemaStep = {
  revision: 'ts_test_0001_probe',
  previous_revision: BASELINE_SEED.alembicRevision,
  apply(tx) {
    tx.run(
      "CREATE TABLE schema_step_probe (probe_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('a', 'b')))",
    );
    tx.run('CREATE INDEX schema_step_probe_kind ON schema_step_probe(kind)');
    tx.run(
      'CREATE TABLE schema_step_probe_child (child_id TEXT PRIMARY KEY, probe_id TEXT NOT NULL REFERENCES schema_step_probe(probe_id))',
    );
    tx.run("INSERT INTO schema_step_probe VALUES ('probe', 'a')");
    tx.run("INSERT INTO schema_step_probe_child VALUES ('child', 'probe')");
  },
};

/** Rebuild the probe table without its CHECK, the way a real step drops a closed list. */
export const rebuild_step: SchemaStep = {
  revision: 'ts_test_0002_rebuild',
  previous_revision: probe_step.revision,
  apply(tx) {
    tx.run('CREATE TABLE schema_step_probe_new (probe_id TEXT PRIMARY KEY, kind TEXT NOT NULL)');
    tx.run(
      'INSERT INTO schema_step_probe_new (probe_id, kind) SELECT probe_id, kind FROM schema_step_probe',
    );
    tx.run('DROP TABLE schema_step_probe');
    tx.run('ALTER TABLE schema_step_probe_new RENAME TO schema_step_probe');
    tx.run('CREATE INDEX schema_step_probe_kind ON schema_step_probe(kind)');
  },
};

export const TEST_SCHEMA_STEPS: readonly SchemaStep[] = [probe_step, rebuild_step];

/** Throws after its DDL ran. */
export const failing_step: SchemaStep = {
  revision: 'ts_test_0002_failing',
  previous_revision: probe_step.revision,
  apply(tx) {
    tx.run('CREATE TABLE schema_step_failed (failed_id TEXT PRIMARY KEY)');
    throw new Error('scripted schema step failure');
  },
};

/** Leaves a row whose parent does not exist. */
export const dangling_fk_step: SchemaStep = {
  revision: 'ts_test_0002_dangling',
  previous_revision: probe_step.revision,
  apply(tx) {
    tx.run(
      'CREATE TABLE schema_step_orphan (orphan_id TEXT PRIMARY KEY, probe_id TEXT NOT NULL REFERENCES schema_step_probe(probe_id))',
    );
    tx.run("INSERT INTO schema_step_orphan VALUES ('orphan', 'missing')");
  },
};
