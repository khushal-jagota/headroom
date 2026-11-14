/** Closed Python/Node release metadata validation, not a runtime admission probe. */
import { isDeepStrictEqual as equal } from 'node:util';
import { is_record as object } from '../../../core/records.ts';
import type { RuntimeContract, RuntimePlatform } from '../contracts.ts';
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
export function parse_optional_runtime_contract(value: unknown): RuntimeContract | null {
  if (value === null || value === undefined) return null;
  if (!object(value)) fail('runtime contract must be an object');
  const node = value.version === 2;
  const required = [
    'version',
    'system',
    'architecture',
    'platforms',
    node ? 'minimum_node_version' : 'python_version',
    'node_major',
    'cpu_features',
    'external_libraries',
    'external_executables',
    'inventory_sha256',
  ];
  if (!equal(Object.keys(value).sort(), [...required].sort()))
    fail('runtime contract has missing or unknown fields');
  if (!equal(value.platforms, SUPPORTED_PLATFORMS)) fail('unsupported runtime platform boundary');
  for (const key of ['version', 'node_major'])
    if (!Number.isInteger(value[key])) fail(`runtime ${key} must be an integer`);
  for (const key of [
    'system',
    'architecture',
    node ? 'minimum_node_version' : 'python_version',
    'inventory_sha256',
  ])
    if (typeof value[key] !== 'string') fail(`runtime ${key} must be a string`);
  for (const key of ['cpu_features', 'external_libraries', 'external_executables'])
    if (
      !Array.isArray(value[key]) ||
      !(value[key] as unknown[]).every((item) => typeof item === 'string')
    )
      fail(`runtime ${key} must be a list of strings`);
  if (value.version !== 1 && !node) fail('unsupported runtime contract version; republish the app');
  if (value.system !== 'linux' || value.architecture !== 'x86_64')
    fail('unsupported runtime operating system or architecture');
  if (value.node_major !== 22) fail('runtime requires host Node major 22');
  if (node && value.minimum_node_version !== MINIMUM_NODE_VERSION)
    fail(`runtime requires minimum Node ${MINIMUM_NODE_VERSION}`);
  if (!node && !fullmatch(/^3\.12\.\p{Decimal_Number}+$/u, value.python_version as string))
    fail('runtime requires a concrete CPython 3.12 patch version');
  if (!fullmatch(/^[0-9a-f]{64}$/, value.inventory_sha256 as string))
    fail('invalid runtime inventory SHA256');
  if (!equal(value.external_libraries, GLIBC_LIBRARIES))
    fail('non-glibc runtime libraries must be bundled');
  if (!equal(value.external_executables, ['sh', 'node']))
    fail('unsupported external runtime executables');
  const features = value.cpu_features as string[];
  if (
    !SUPPORTED_CPU_FEATURES.every((feature) => features.includes(feature)) ||
    new Set(features).size !== features.length ||
    features.some((feature) => !fullmatch(/^[a-z0-9_]+$/, feature))
  )
    fail('invalid required CPU features');
  return {
    ...value,
    version: value.version,
    node_major: value.node_major,
    platforms: SUPPORTED_PLATFORMS.map((platform) => ({ ...platform })),
  } as unknown as RuntimeContract;
}
