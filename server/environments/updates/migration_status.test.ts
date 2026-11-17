import assert from 'node:assert/strict';
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { it } from 'vitest';
import { createDatabaseFromSeed } from '../../core/database/index.ts';
import { build_schema_artifacts } from '../../core/database/tools/generate_seed.ts';
import {
  BASELINE_SEED,
  TEST_SCHEMA_STEPS,
  probe_step,
  rebuild_step,
} from '../../core/database/schema_steps/testing/steps.ts';
import { BASELINE_MIGRATION_GRAPH_PATH, MIGRATION_GRAPH_PATH } from '../release/migrations.ts';
import type { MigrationGraph } from '../release/contracts.ts';
import { create_migration_status } from './migration_status.ts';

it('accepts the head and TypeScript ancestors; refuses Python ancestors and unknown revisions; a missing baseline means an empty chain', async () => {
  // The ancestor rule decides the backup and admits the install. A temporary staged app.
  const repository = resolve(import.meta.dirname, '../../..');
  const root = mkdtempSync(join(tmpdir(), 'panels-node-frontier-'));
  try {
    const staged = join(root, 'staged');
    mkdirSync(dirname(join(staged, BASELINE_MIGRATION_GRAPH_PATH)), { recursive: true });
    cpSync(
      join(repository, BASELINE_MIGRATION_GRAPH_PATH),
      join(staged, BASELINE_MIGRATION_GRAPH_PATH),
    );
    mkdirSync(dirname(join(staged, MIGRATION_GRAPH_PATH)), { recursive: true });
    writeFileSync(
      join(staged, MIGRATION_GRAPH_PATH),
      (await build_schema_artifacts(repository, TEST_SCHEMA_STEPS)).graph,
    );
    const baseline = JSON.parse(
      readFileSync(join(staged, BASELINE_MIGRATION_GRAPH_PATH), 'utf8'),
    ) as MigrationGraph;
    const python_ancestor = baseline.revisions[BASELINE_SEED.alembicRevision]![0]!;
    const at = (revision: string) => {
      const path = join(root, revision + '.sqlite');
      if (existsSync(path)) return path;
      createDatabaseFromSeed(
        path,
        {
          statements: [
            'CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)',
          ],
          alembicRevision: revision,
        },
        () => {},
      ).close();
      return path;
    };
    const status = create_migration_status();
    const outcome = async (revision: string) => {
      try {
        return await status.node_schema_steps_pending(at(revision), staged);
      } catch (error) {
        return (error as Error).message;
      }
    };
    const refused =
      'This release requires the database to be at exactly its schema revision. ' +
      'Install the Python release that carries the same migrations first.';
    assert.deepEqual(
      await Promise.all(
        [
          rebuild_step.revision,
          probe_step.revision,
          BASELINE_SEED.alembicRevision,
          python_ancestor,
          'unknown_revision',
        ].map(outcome),
      ),
      [false, true, true, refused, refused],
    );

    // Without a baseline file the chain is empty: only the head is accepted.
    rmSync(join(staged, BASELINE_MIGRATION_GRAPH_PATH));
    assert.deepEqual(await Promise.all([rebuild_step.revision, probe_step.revision].map(outcome)), [
      false,
      refused,
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
