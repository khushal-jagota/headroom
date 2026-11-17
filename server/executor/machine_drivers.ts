/** Which backends a remote machine can run. */
import type { BackendInstanceId } from '../drivers/core/identity.ts';

/**
 * The backend keys the protocol-26 Mac runtime accepts. Until a machine advertises its
 * drivers, every remote machine is taken to run exactly these.
 */
export const PYTHON_MAC_BACKEND_KEYS: ReadonlySet<string> = new Set(['hermes', 'codex', 'claude']);

/** The home's backend ids a remote machine can run, in the home's order. */
export function machine_driver_ids(
  home_ids: readonly BackendInstanceId[],
): readonly BackendInstanceId[] {
  return home_ids.filter((id) => PYTHON_MAC_BACKEND_KEYS.has(id));
}
