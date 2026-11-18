/**
 * Release runtime contract shape. The values are the release's own requirements: admission
 * checks this host against them, and never against this version's own constants. The
 * constants below are what this version writes into the releases it builds.
 */
import { is_record as object } from '../../../core/records.ts';
import type { DarwinNodeRuntimeContract, RuntimeContract, RuntimePlatform } from '../contracts.ts';
export const SUPPORTED_PLATFORMS: readonly RuntimePlatform[] = [
  { distribution: 'debian', version: '13', minimum_glibc: '2.41' },
  { distribution: 'ubuntu', version: '24.04', minimum_glibc: '2.39' },
];
export const SUPPORTED_CPU_FEATURES = [
  'avx',
  'avx2',
  'bmi1',
  'bmi2',
  'cx16',
  'f16c',
  'fma',
  'lahf_lm',
  'abm',
  'movbe',
  'popcnt',
  'sse2',
  'sse4_1',
  'sse4_2',
  'ssse3',
  'xsave',
] as const;
export const GLIBC_LIBRARIES = [
  'ld-linux-x86-64.so.2',
  'libc.so.6',
  'libdl.so.2',
  'libm.so.6',
  'libpthread.so.0',
  'libresolv.so.2',
  'librt.so.1',
  'libutil.so.1',
] as const;
export const MINIMUM_NODE_VERSION = '22.18.0' as const;
export class RuntimeContractError extends Error {}
function fail(message: string): never {
  throw new RuntimeContractError(message);
}
function fullmatch(pattern: RegExp, value: string): boolean {
  return pattern.exec(value)?.[0] === value;
}
function strings(value: unknown): boolean {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}
/** Host checks compare these with this machine, so they must be readable numbers. */
function versions(value: Record<string, unknown>): void {
  if (!fullmatch(/^\d+\.\d+\.\d+$/, value.minimum_node_version as string))
    fail('invalid runtime minimum Node version');
  if (value.system === 'darwin' && !fullmatch(/^\d+(?:\.\d+)*$/, value.minimum_os as string))
    fail('invalid Mac runtime minimum OS');
  if (!fullmatch(/^[0-9a-f]{64}$/, value.inventory_sha256 as string))
    fail('invalid runtime inventory SHA256');
}
function parse_darwin_contract(value: Record<string, unknown>): DarwinNodeRuntimeContract {
  for (const key of [
    'system',
    'architecture',
    'role',
    'minimum_os',
    'minimum_node_version',
    'inventory_sha256',
  ])
    if (typeof value[key] !== 'string')
      fail('Mac runtime contract identity fields must be strings');
  if (!strings(value.external_libraries))
    fail('runtime external_libraries must be a list of strings');
  if (
    value.version !== 2 ||
    value.system !== 'darwin' ||
    value.architecture !== 'arm64' ||
    value.role !== 'home'
  )
    fail('runtime requires a darwin arm64 home artifact');
  if (!Number.isInteger(value.node_major)) fail('runtime node_major must be an integer');
  versions(value);
  return {
    ...value,
    external_libraries: [...(value.external_libraries as string[])],
  } as unknown as DarwinNodeRuntimeContract;
}
export function parse_optional_runtime_contract(value: unknown): RuntimeContract | null {
  if (value === null || value === undefined) return null;
  if (!object(value)) fail('runtime contract must be an object');
  if (value.system === 'darwin') return parse_darwin_contract(value);
  if (value.version !== 2) fail('unsupported runtime contract version; republish the app');
  for (const key of ['version', 'node_major'])
    if (!Number.isInteger(value[key])) fail(`runtime ${key} must be an integer`);
  for (const key of ['system', 'architecture', 'minimum_node_version', 'inventory_sha256'])
    if (typeof value[key] !== 'string') fail(`runtime ${key} must be a string`);
  for (const key of ['cpu_features', 'external_libraries', 'external_executables'])
    if (!strings(value[key])) fail(`runtime ${key} must be a list of strings`);
  if (value.system !== 'linux' || value.architecture !== 'x86_64')
    fail('unsupported runtime operating system or architecture');
  if (
    !Array.isArray(value.platforms) ||
    !value.platforms.every(
      (platform) =>
        object(platform) &&
        ['distribution', 'version', 'minimum_glibc'].every(
          (key) => typeof platform[key] === 'string',
        ),
    )
  )
    fail('runtime platforms must name a distribution, version and minimum glibc');
  versions(value);
  return {
    ...value,
    version: value.version,
    node_major: value.node_major,
    platforms: (value.platforms as RuntimePlatform[]).map((platform) => ({ ...platform })),
  } as unknown as RuntimeContract;
}
