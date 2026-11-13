/** Source application identity rules without filesystem or runtime probes. */
import { JsonNumber } from '../../../core/http/numeric_json.ts';
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
function numeric(value: unknown): bigint | number | undefined {
  if (typeof value === 'boolean') return BigInt(Number(value));
  if (value instanceof JsonNumber) return value.integer ?? value.value;
  if (typeof value === 'number') return Number.isInteger(value) ? BigInt(value) : value;
  return undefined;
}
/** Python dict equality admits numeric equivalents in the separate payload JSON. */
export function python_json_equal(left: unknown, right: unknown): boolean {
  const a = numeric(left),
    b = numeric(right);
  if (a !== undefined || b !== undefined) {
    if (a === undefined || b === undefined) return false;
    if (typeof a === typeof b) return a === b;
    const integer = typeof a === 'bigint' ? a : b;
    const floating = typeof a === 'number' ? a : b;
    return (
      typeof floating === 'number' &&
      Number.isFinite(floating) &&
      Number.isInteger(floating) &&
      BigInt(floating) === integer
    );
  }
  if (Array.isArray(left) || Array.isArray(right))
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => python_json_equal(value, right[index]))
    );
  if ((left !== null && typeof left === 'object') || (right !== null && typeof right === 'object'))
    return (
      left !== null &&
      typeof left === 'object' &&
      right !== null &&
      typeof right === 'object' &&
      Object.keys(left).length === Object.keys(right).length &&
      Object.entries(left).every(
        ([key, value]) =>
          Object.hasOwn(right, key) &&
          python_json_equal(value, (right as Record<string, unknown>)[key]),
      )
    );
  return left === right;
}
