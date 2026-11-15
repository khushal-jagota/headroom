/** Fresh-home-only entry. Seed and bootstrap code come from this admitted application. */
import { lstatSync, readFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { createDatabaseFromSeed, checkpointDatabase } from '../core/database/index.ts';
import type { ApplicationSchemaSeed } from '../core/database/contracts.ts';
import { bootstrap_application } from '../environments/bootstrap.ts';

try {
  if (process.argv.length !== 3 || !isAbsolute(process.argv[2]!) || process.argv[2]!.includes('\0'))
    throw new Error('Expected one absolute fresh home database path');
  const path = process.argv[2]!;
  // Refuse the original spelling before a test-path resolver can follow a symlink.
  try {
    lstatSync(path);
    throw new Error('Fresh home database path already exists');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const seed = JSON.parse(
    readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as ApplicationSchemaSeed;
  const database = createDatabaseFromSeed(path, seed, (tx) =>
    bootstrap_application(tx, Math.floor(Date.now() / 1000)),
  );
  try {
    checkpointDatabase(database, 'TRUNCATE');
  } finally {
    database.close();
  }
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
