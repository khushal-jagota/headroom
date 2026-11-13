/** Source host policy over observations. Collecting those observations stays in admission. */
import type { RuntimeContract, NodeRuntimeContract } from '../contracts.ts';
import { AppValidationError } from '../contracts.ts';

export interface RuntimeHostFacts {
  system: string;
  architecture: string;
  distribution: string;
  version: string;
  glibc: string | null;
  feature_sets: readonly (readonly string[])[];
}
function at_least(found: readonly number[], minimum: readonly number[]): boolean {
  for (let index = 0; index < Math.max(found.length, minimum.length); index++) {
    const left = found[index] ?? 0,
      right = minimum[index] ?? 0;
    if (left !== right) return left > right;
  }
  return true;
}
export function verify_runtime_host_facts(
  contract: RuntimeContract,
  facts: RuntimeHostFacts,
): void {
  if (facts.system !== contract.system || facts.architecture !== contract.architecture)
    throw new AppValidationError('runtime requires Linux x86_64');
  const supported = contract.platforms.find(
    (item) => item.distribution === facts.distribution && item.version === facts.version,
  );
  if (!supported) throw new AppValidationError('unsupported runtime host: ');
  if (
    facts.glibc === null ||
    !/^\d+(?:\.\d+)*$/.test(facts.glibc) ||
    !at_least(facts.glibc.split('.').map(Number), supported.minimum_glibc.split('.').map(Number))
  )
    throw new AppValidationError('unsupported runtime host: unsupported glibc version');
  if (
    !facts.feature_sets.length ||
    facts.feature_sets.some(
      (flags) => !contract.cpu_features.every((feature) => flags.includes(feature)),
    )
  )
    throw new AppValidationError('unsupported runtime host: required CPU features are unavailable');
}
export function require_minimum_node(version: string, contract: NodeRuntimeContract): void {
  const text = version.trim(),
    match = /^(\d+)\.(\d+)\.(\d+)$/.exec(text);
  if (!match || match[0] !== text)
    throw new AppValidationError(
      `unreadable Node version: ${Array.from(version).slice(0, 40).join('')}`,
    );
  const found = match.slice(1).map(Number),
    minimum = contract.minimum_node_version.split('.').map(Number);
  if (found[0] !== contract.node_major || !at_least(found, minimum))
    throw new AppValidationError(
      `runtime requires Node ${contract.minimum_node_version} or a later Node ${contract.node_major}`,
    );
}
export function require_host_node(version: string, contract: RuntimeContract): void {
  if (version.split('.')[0] !== `v${contract.node_major}`)
    throw new AppValidationError(`runtime requires Node ${contract.node_major}`);
  if (contract.version === 2) require_minimum_node(version.replace(/^v/, ''), contract);
}
/** Python's os-release data reader: quotes and shell escapes are data, never evaluated. */
export function os_release_values(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const match = /^([A-Za-z0-9_]+)=(.*)$/.exec(line);
    if (!match) continue;
    let value = match[2]!;
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    )
      value = value.slice(1, -1);
    values[match[1]!] = value.replace(/\\([\\$"'`])/g, '$1');
  }
  return values;
}
