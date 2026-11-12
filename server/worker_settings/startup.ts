import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import type { Config } from '../core/config.ts';
import type { Database } from '../core/database/contracts.ts';
import { seed_packaged_skills } from '../skills/data.ts';
import {
  write_skill_home,
  provision_native_backend_skills,
  provision_workspace_skills,
} from '../skills/sources.ts';
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
  if (config.workspace_root !== null) provision_workspace_skills(config.workspace_root, parent);
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const installed = !!config.app_sha && resolve(root) === join(dirname(parent), 'app');
  // A test process never provisions native homes. Its explicit workspace and data paths remain isolated.
  if (installed && !config.test_mode) {
    const home = homedir();
    const hermes = process.env.PLAN_HERMES_HOME || join(home, '.hermes');
    provision_native_backend_skills(
      hermes === '~' ? home : hermes.startsWith('~/') ? join(home, hermes.slice(2)) : hermes,
      parent,
    );
    provision_native_backend_skills(join(home, '.codex'), parent);
    provision_native_backend_skills(join(home, '.claude'), parent);
  }
}
