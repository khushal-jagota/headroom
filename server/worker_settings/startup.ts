import { dirname, resolve } from 'node:path';
import type { Config } from '../core/config.ts';
import type { Database } from '../core/database/contracts.ts';
import { seed_packaged_skills } from '../skills/data.ts';
import { write_skill_home } from '../skills/sources.ts';
import { seed_shipped_worker_types, seed_chief_settings } from '../worker_types/shipped.ts';
import { load_worker_runtime_definitions } from '../worker_types/configuration.ts';
/** Publish the canonical rows before routes become available. Python still owns schema upgrades. */
export function initialize_worker_settings(database: Database, config: Config): void {
  database.write((tx) => {
    seed_packaged_skills(tx);
    seed_shipped_worker_types(tx);
    seed_chief_settings(tx);
  });
  database.read(load_worker_runtime_definitions);
  const parent = dirname(resolve(config.db_path));
  write_skill_home(database, parent);
}
