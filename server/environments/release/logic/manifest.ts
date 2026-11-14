/** Source application identity rules without filesystem or runtime probes. */
import type { AppManifest } from '../contracts.ts';
import { AppValidationError } from '../contracts.ts';
import { APP_FORMAT } from '../configuration.ts';

export function validate_app_sha(value: string, label = 'app SHA'): string {
  if (!/^[0-9a-f]{40}$/.test(value) || value.length !== 40)
    throw new AppValidationError(`${label} must be a 40-character lowercase Git SHA`);
  return value;
}
export function manifest_identity(
  value: unknown,
  expected_sha?: string,
): Omit<AppManifest, 'runtime_contract'> {
  if (
    value === null ||
    typeof value !== 'object' ||
    Array.isArray(value) ||
    !('format' in value) ||
    value.format !== APP_FORMAT
  )
    throw new AppValidationError('app manifest has an unsupported format');
  const raw = value as Record<string, unknown>;
  if (typeof raw.app_sha !== 'string')
    throw new AppValidationError('app manifest is missing app SHA');
  validate_app_sha(raw.app_sha);
  if (expected_sha !== undefined && raw.app_sha !== validate_app_sha(expected_sha, 'expected SHA'))
    throw new AppValidationError('app manifest SHA does not match expected SHA');
  for (const field of ['source_digest', 'artifact_digest'])
    if (
      typeof raw[field] !== 'string' ||
      !/^[0-9a-f]{64}$/.test(raw[field]) ||
      raw[field].length !== 64
    )
      throw new AppValidationError(`app manifest has an invalid ${field.replace('_', ' ')}`);
  return {
    app_sha: raw.app_sha,
    source_digest: raw.source_digest as string,
    artifact_digest: raw.artifact_digest as string,
  };
}
