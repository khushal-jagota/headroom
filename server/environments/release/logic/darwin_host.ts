/** Native host admission uses macOS product versions, not Darwin kernel versions. */
import type { DarwinNodeRuntimeContract } from '../contracts.ts';
import { AppValidationError } from '../contracts.ts';

export function verify_darwin_host_facts(
  contract: DarwinNodeRuntimeContract,
  system: string,
  architecture: string,
  version: string,
): void {
  if (system !== contract.system || architecture !== contract.architecture)
    throw new AppValidationError('runtime requires macOS arm64');
  const text = version.trim();
  if (!/^\d+(?:\.\d+)*$/.test(text))
    throw new AppValidationError('Mac product version is unreadable');
  const found = text.split('.').map(Number),
    minimum = contract.minimum_os.split('.').map(Number);
  for (let index = 0; index < Math.max(found.length, minimum.length); index++) {
    if ((found[index] ?? 0) > (minimum[index] ?? 0)) return;
    if ((found[index] ?? 0) < (minimum[index] ?? 0))
      throw new AppValidationError('Mac is older than the runtime minimum OS');
  }
}
