import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import {
  createDatabase,
  createDatabaseFromSeed,
  type ApplicationSchemaSeed,
} from '../core/database/index.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { StallChecker } from '../stalls/checker.ts';
import { ProcessWaitRegistry } from '../stalls/registry.ts';
import { bootstrap_application } from './bootstrap.ts';

test('a fresh install holds only the Home machine and raises no stall for an absent Mac', () => {
  // The stall checker still groups unplaced waits under the original Mac id. On a fresh
  // install that machine does not exist, and nothing may report it as stalled.
  const root = mkdtempSync(join(tmpdir(), 'panels-bootstrap-'));
  try {
    const path = join(root, 'planning.db');
    const seed = JSON.parse(
      readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
    ) as ApplicationSchemaSeed;
    createDatabaseFromSeed(path, seed, (tx) => bootstrap_application(tx, 0)).close();
    const database = createDatabase(path);
    try {
      expect(
        database.read((sql) => ({
          machines: sql.all('SELECT machine_id FROM machines'),
          credentials: sql.all('SELECT machine_id FROM machine_executor_credentials'),
        })),
      ).toEqual({ machines: [{ machine_id: HOME_MACHINE_ID }], credentials: [] });
      const now_ms = 3_600_000;
      const checker = new StallChecker(
        database,
        new ProcessWaitRegistry(() => now_ms),
        () => [],
        () => now_ms,
      );
      expect(checker.check_once()).toEqual([]);
      expect(database.read((sql) => sql.all('SELECT finding_key FROM stall_findings'))).toEqual([]);
    } finally {
      database.close();
    }
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
