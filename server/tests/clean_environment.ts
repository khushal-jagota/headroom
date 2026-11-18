import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';

/** Every variable through which a built-in driver's provider home reaches its CLI. */
const PROVIDER_HOME_NAMES: readonly string[] = BUILT_IN_MANIFESTS.flatMap(({ manifest }) =>
  manifest.home === undefined
    ? []
    : [manifest.home.environment_name, ...(manifest.home.extra_environment_names ?? [])],
);

export function clean_test_environment(): () => void {
  for (const name of Object.keys(process.env)) {
    if (name.startsWith('PLAN_') || PROVIDER_HOME_NAMES.includes(name)) {
      delete process.env[name];
    }
  }
  const home = mkdtempSync(join(tmpdir(), 'panels-vitest-home-'));
  process.env.HOME = home;
  return () => {
    rmSync(home, { recursive: true, force: true });
  };
}
