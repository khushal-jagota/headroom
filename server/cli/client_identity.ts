/** Diagnostic identity for this CLI installation. Authentication remains separate. */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { homedir, hostname } from 'node:os';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import type { ClientIdentity } from '../client_versions/contracts.ts';

let version: string | undefined;
function installed_version(): string {
  if (version !== undefined) return version;
  const root = new URL('../../', import.meta.url);
  try {
    const manifest = JSON.parse(readFileSync(new URL('manifest.json', root), 'utf8')) as {
      app_sha?: unknown;
    };
    if (typeof manifest.app_sha === 'string' && /^[a-f0-9]{40}$/.test(manifest.app_sha))
      return (version = manifest.app_sha);
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  const revision = spawnSync('git', ['-C', fileURLToPath(root), 'rev-parse', 'HEAD'], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).stdout?.trim();
  return (version = revision && /^[a-f0-9]{40}$/.test(revision) ? revision : 'development');
}

export function cli_client_identity(env: NodeJS.ProcessEnv): ClientIdentity {
  const device_id =
    env.PLAN_EXECUTOR_ID?.trim() ||
    createHash('sha256')
      .update(JSON.stringify([hostname(), homedir()]))
      .digest('hex');
  return { kind: 'cli', device_id, version: installed_version() };
}
