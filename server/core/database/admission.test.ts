import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { it } from 'vitest';
import { application_schema_seed, require_application_schema } from './admission.ts';
import { applySchemaSteps, createDatabaseFromSeed } from './index.ts';
import { probe_step } from './schema_steps/testing/steps.ts';

it('a previous app refuses a database one step ahead before opening a writer', () => {
  // A manual rollback must not start the old app on stepped data. Real SQLite.
  const root = mkdtempSync(join(tmpdir(), 'panels-admission-ahead-'));
  try {
    const path = join(root, 'planner.db');
    createDatabaseFromSeed(path, application_schema_seed, () => {}).close();
    applySchemaSteps(path, [probe_step], {
      head_revision: probe_step.revision,
      expected_statements: null,
      refuse_pending: false,
    });
    // A reader may create an empty WAL. Neither file may gain a byte.
    const files = () =>
      [path, path + '-wal'].map((file) =>
        createHash('sha256')
          .update(existsSync(file) ? readFileSync(file) : '')
          .digest('hex'),
      );
    const before = files();
    assert.throws(
      () => require_application_schema(path),
      new RegExp(
        `actual=\\["${probe_step.revision}"\\]; expected=\\["${application_schema_seed.alembicRevision}"\\]`,
      ),
    );
    assert.deepEqual(files(), before);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
