/**
 * The built-in drivers' static halves: manifest, client, model rules and tool-detail reading, in
 * BUILT_IN_DRIVERS order. Installer-side code and SQL-level callers read it.
 */
import { BUILT_IN_DRIVERS } from './built_in.ts';
import type { AnyAgentDriver } from './core/driver.ts';
import { default_instance_id_for_driver } from './core/identity.ts';
import type { BackendInstanceId } from './core/identity.ts';

export type BuiltInDriverFacts = Pick<
  AnyAgentDriver,
  | 'driver_kind'
  | 'manifest'
  | 'client'
  | 'models'
  | 'launch_model_refusal'
  | 'readable_tool_call_detail'
>;
/** BUILT_IN_DRIVERS itself. driver.ts loads its runtime only through import(), so this loads none. */
export const BUILT_IN_MANIFESTS: readonly BuiltInDriverFacts[] = BUILT_IN_DRIVERS;
/** Each built-in driver's default instance id. Card order and the unknown-backend choice list. */
export const KNOWN_BACKEND_INSTANCE_IDS: readonly BackendInstanceId[] = BUILT_IN_MANIFESTS.map(
  (driver) => default_instance_id_for_driver(driver.driver_kind),
);
/** The built-in driver behind a default instance id, or undefined. */
export function built_in_driver_of(id: string): BuiltInDriverFacts | undefined {
  return BUILT_IN_MANIFESTS.find(
    (driver) => default_instance_id_for_driver(driver.driver_kind) === id,
  );
}
