/** Fresh-home-only entry. Seed and bootstrap code come from this admitted application. */
import { lstatSync, readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { createDatabaseFromSeed, checkpointDatabase } from '../core/database/index.ts';
import type { ApplicationSchemaSeed } from '../core/database/contracts.ts';
import { issue_home_machine_credential } from '../machines/executor_credentials.ts';
import { credential_path, write_credential } from '../executor/credential_file.ts';
import { join } from 'node:path';
import { bootstrap_application } from '../environments/bootstrap.ts';
import { timezone } from '../measures/logic/dates.ts';

try {
  if (
    ![3, 4].includes(process.argv.length) ||
    !isAbsolute(process.argv[2]!) ||
    process.argv[2]!.includes('\0')
  )
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
  const zone = process.env.PLAN_INSTALL_OWNER_TIME_ZONE;
  const owner_time_zone = zone === undefined ? undefined : timezone(zone);
  const database = createDatabaseFromSeed(path, seed, (tx) =>
    bootstrap_application(tx, Math.floor(Date.now() / 1000), owner_time_zone),
  );
  try {
    if (process.argv[3]) {
      const machine_root = process.argv[3];
      if (!isAbsolute(machine_root) || machine_root.includes('\0'))
        throw new Error('Expected an absolute machine state path');
      // The first machine is the server's own machine, so it takes the home identity.
      const grant = database.write((tx) => issue_home_machine_credential(tx, process.platform));
      await write_credential(credential_path(machine_root), grant.credential);
      writeFileSync(
        join(machine_root, 'identity.json'),
        JSON.stringify({ machine_id: grant.machine_id }) + '\n',
        { flag: 'wx', mode: 0o600 },
      );
    }
    checkpointDatabase(database, 'TRUNCATE');
  } finally {
    database.close();
  }
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
