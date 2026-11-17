/** Which backends a remote machine can run. */
import type { BackendInstanceId } from '../drivers/core/identity.ts';
import { built_in_driver_of, KNOWN_BACKEND_INSTANCE_IDS } from '../drivers/built_in_manifests.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { read_presence } from './vps_presence_store.ts';

/**
 * The fallback for a machine that advertises no drivers: the backend keys the Python Mac
 * runtime accepts. Kept until the Manager lifts Python Mac compatibility.
 */
export const PYTHON_MAC_BACKEND_KEYS: ReadonlySet<string> = new Set(['hermes', 'codex', 'claude']);

/** The driver kinds a machine's latest stored lease advertises, or the fallback set. */
export function machine_driver_kinds(sql: ReadSql, machine_id: string): ReadonlySet<string> {
  const advertised = read_presence(sql, machine_id)?.lease?.driver_kinds;
  return advertised === undefined ? PYTHON_MAC_BACKEND_KEYS : new Set(advertised);
}

/** A backend id without a built-in driver runs on no remote machine. */
export function machine_can_run(
  driver_kinds: ReadonlySet<string>,
  backend_key: BackendInstanceId,
): boolean {
  const driver_kind = built_in_driver_of(backend_key)?.driver_kind;
  return driver_kind !== undefined && driver_kinds.has(driver_kind);
}

/** The known backend ids a machine runs, in KNOWN_BACKEND_INSTANCE_IDS order. */
export function machine_backend_ids(
  driver_kinds: ReadonlySet<string>,
): readonly BackendInstanceId[] {
  return KNOWN_BACKEND_INSTANCE_IDS.filter((id) => machine_can_run(driver_kinds, id));
}
