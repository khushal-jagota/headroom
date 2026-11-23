import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { CLIENT_COMPATIBILITY } from './policy.ts';
import type { ClientVersionMeta, ReleaseParts } from './contracts.ts';

/** Describe the installed release, never the release available for download. */
export function client_version_meta(
  root: string,
  expected_sha: string | null = null,
): ClientVersionMeta {
  let manifest: Record<string, unknown>;
  try {
    manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT')
      return { compatibility: CLIENT_COMPATIBILITY };
    throw error;
  }
  if (expected_sha !== null && manifest.app_sha !== expected_sha)
    return { compatibility: CLIENT_COMPATIBILITY };
  const result: ReturnType<typeof client_version_meta> = { compatibility: CLIENT_COMPATIBILITY };
  if (Number.isSafeInteger(manifest.release_number) && (manifest.release_number as number) > 0)
    result.release_number = manifest.release_number as number;
  if (
    manifest.parts !== null &&
    typeof manifest.parts === 'object' &&
    !Array.isArray(manifest.parts)
  )
    result.parts = manifest.parts as unknown as ReleaseParts;
  return result;
}
