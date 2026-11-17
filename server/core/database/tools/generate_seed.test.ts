import assert from 'node:assert/strict';
import { appendFileSync, cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { it } from 'vitest';
import { check_schema_artifacts, write_schema_artifacts } from './generate_seed.ts';
import {
  BASELINE_MIGRATION_GRAPH_PATH,
  BASELINE_SEED_PATH,
  SEED_PATH,
} from '../../../environments/release/migrations.ts';
import { TEST_SCHEMA_STEPS, probe_step } from '../schema_steps/testing/steps.ts';

it('--check detects seed edits, Python migration drift and a misanchored chain', async () => {
  // Drift between the baseline, the steps and the live files must not go unnoticed.
  const repository = resolve(import.meta.dirname, '../../../..');
  const root = mkdtempSync(join(tmpdir(), 'panels-generate-seed-'));
  try {
    const versions = 'src/planner/core/migrations/versions';
    for (const path of [BASELINE_SEED_PATH, BASELINE_MIGRATION_GRAPH_PATH, versions]) {
      mkdirSync(dirname(join(root, path)), { recursive: true });
      cpSync(join(repository, path), join(root, path), { recursive: true });
    }
    mkdirSync(join(root, 'server/environments/release'), { recursive: true });
    const written = await write_schema_artifacts(root, TEST_SCHEMA_STEPS);
    assert.deepEqual(await check_schema_artifacts(root, TEST_SCHEMA_STEPS), written);

    appendFileSync(join(root, SEED_PATH), ' ');
    await assert.rejects(
      check_schema_artifacts(root, TEST_SCHEMA_STEPS),
      /application_schema\.json differs/,
    );
    writeFileSync(join(root, SEED_PATH), written.seed);

    writeFileSync(join(root, versions, 'zz_new_python_migration.py'), '# drift\n');
    await assert.rejects(
      check_schema_artifacts(root, TEST_SCHEMA_STEPS),
      /differs from the current Python migration source/,
    );
    rmSync(join(root, versions, 'zz_new_python_migration.py'));

    await assert.rejects(
      check_schema_artifacts(root, [{ ...probe_step, previous_revision: 'goal_holders' }]),
      /follows goal_holders, not/,
    );
    await check_schema_artifacts(root, TEST_SCHEMA_STEPS);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
