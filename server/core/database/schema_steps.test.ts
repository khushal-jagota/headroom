import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterEach, it } from 'vitest';
import {
  applySchemaSteps,
  createDatabaseFromSeed,
  createReadAccess,
  SEED_STATEMENT_QUERY,
} from './index.ts';
import { build_schema_artifacts } from './tools/generate_seed.ts';
import type { ApplicationSchemaSeed } from './contracts.ts';
import {
  BASELINE_SEED,
  TEST_SCHEMA_STEPS,
  dangling_fk_step,
  failing_step,
  probe_step,
  rebuild_step,
} from './schema_steps/testing/steps.ts';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function baseline_database(): string {
  const root = mkdtempSync(join(tmpdir(), 'panels-schema-steps-'));
  roots.push(root);
  const path = join(root, 'planner.db');
  createDatabaseFromSeed(path, BASELINE_SEED, () => {}).close();
  return path;
}
function state(path: string) {
  const reader = createReadAccess(path);
  try {
    return reader.read((sql) => ({
      revision: sql
        .all<{ version_num: string }>('SELECT version_num FROM alembic_version')
        .map((row) => row.version_num),
      statements: sql.all<{ sql: string }>(SEED_STATEMENT_QUERY).map((row) => row.sql),
    }));
  } finally {
    reader.close();
  }
}

it('a failing step rolls back only itself', () => {
  // Partial DDL must never commit. Real SQLite on the baseline seed.
  for (const broken of [failing_step, dangling_fk_step]) {
    const path = baseline_database();
    assert.throws(
      () =>
        applySchemaSteps(path, [probe_step, broken], {
          head_revision: broken.revision,
          expected_statements: null,
          refuse_pending: false,
        }),
      broken === failing_step ? /scripted schema step failure/ : /foreign key violation/,
    );
    const after = state(path);
    assert.deepEqual(after.revision, [probe_step.revision]);
    assert(after.statements.some((sql) => sql.startsWith('CREATE TABLE schema_step_probe ')));
    assert(
      !after.statements.some((sql) => /schema_step_(failed|orphan)/.test(sql)),
      'the broken step left its DDL',
    );
  }
});

it('migrated database equals the generated seed; a one-character difference refuses and rolls back the last step', async () => {
  // Fresh and migrated databases must not drift apart. The generator builds the expected seed.
  const repository = resolve(import.meta.dirname, '../../..');
  const seed = JSON.parse(
    (await build_schema_artifacts(repository, TEST_SCHEMA_STEPS)).seed,
  ) as ApplicationSchemaSeed;
  assert.equal(seed.alembicRevision, rebuild_step.revision);

  const migrated = baseline_database();
  assert.deepEqual(
    applySchemaSteps(migrated, TEST_SCHEMA_STEPS, {
      head_revision: rebuild_step.revision,
      expected_statements: seed.statements,
      refuse_pending: false,
    }),
    {
      from_revision: BASELINE_SEED.alembicRevision,
      to_revision: rebuild_step.revision,
      applied: [probe_step.revision, rebuild_step.revision],
    },
  );
  assert.deepEqual(state(migrated), {
    revision: [rebuild_step.revision],
    statements: seed.statements,
  });
  // The rebuild kept the rows and dropped the CHECK.
  const reader = createReadAccess(migrated);
  try {
    assert.deepEqual(
      reader.read((sql) => sql.all('SELECT probe_id, kind FROM schema_step_probe')),
      [{ probe_id: 'probe', kind: 'a' }],
    );
  } finally {
    reader.close();
  }

  const statements = [...seed.statements];
  const last = statements.length - 1;
  statements[last] = statements[last]! + ' ';
  const refused = baseline_database();
  assert.throws(
    () =>
      applySchemaSteps(refused, TEST_SCHEMA_STEPS, {
        head_revision: rebuild_step.revision,
        expected_statements: statements,
        refuse_pending: false,
      }),
    /differs from this release's seed/,
  );
  const after = state(refused);
  assert.deepEqual(after.revision, [probe_step.revision]);
  assert(after.statements.some((sql) => sql.includes("CHECK (kind IN ('a', 'b'))")));
});
