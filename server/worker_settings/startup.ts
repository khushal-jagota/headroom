import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Config } from '../core/config.ts';
import type { Database } from '../core/database/contracts.ts';
import { seed_packaged_skills } from '../skills/data.ts';
import { write_skill_home } from '../skills/sources.ts';
import { seed_shipped_worker_types, seed_chief_settings } from '../worker_types/shipped.ts';
import { account_skill_homes } from '../skills/account.ts';
import { Worker } from 'node:worker_threads';
import type { SkillPublicationInput } from './skill_publication_worker.ts';
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
/**
 * External provider and workspace effects start only after an update trial releases work.
 * Machine code links the provider account skill folders. Before, an installed home linked them by
 * default, so an installed home removes its old links there.
 */
export async function publish_worker_skills(config: Config): Promise<void> {
  const parent = dirname(resolve(config.db_path));
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const installed = !!config.app_sha && resolve(root) === join(dirname(parent), 'app');
  const account_homes = installed ? account_skill_homes() : null;
  if (config.workspace_root === null && account_homes === null) return;
  const input: SkillPublicationInput = { parent, workspace: config.workspace_root, account_homes };
  const worker = new Worker(new URL('./skill_publication_worker.ts', import.meta.url), {
    workerData: input,
  });
  const warning = await new Promise<string | null>((resolve, reject) => {
    let result: { warning?: string | null; error?: string } | undefined;
    let failure: Error | undefined;
    worker.once('message', (value) => {
      result = value;
    });
    worker.once('error', (error) => {
      failure = error;
    });
    // A reply does not prove that the worker released its filesystem handles.
    worker.once('exit', (code) => {
      if (failure) reject(failure);
      else if (code !== 0 || !result)
        reject(new Error('Skill publication worker exited without a result'));
      else if (result.error) reject(new Error(result.error));
      else resolve(result.warning ?? null);
    });
  });
  if (warning) console.warn(warning);
}
