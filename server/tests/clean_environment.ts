import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export function clean_test_environment(): () => void {
  for (const name of Object.keys(process.env)) {
    if (
      name.startsWith('PLAN_') ||
      ['CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'HERMES_HOME'].includes(name)
    ) {
      delete process.env[name];
    }
  }
  const home = mkdtempSync(join(tmpdir(), 'panels-vitest-home-'));
  process.env.HOME = home;
  return () => {
    rmSync(home, { recursive: true, force: true });
  };
}
