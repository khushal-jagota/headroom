/** Driver kinds and backend instance ids: one slug grammar, two brands. (T3 providerInstance.ts) */
import { PlannerError } from '../../core/contracts.ts';

export const DRIVER_SLUG_PATTERN = /^[a-zA-Z][a-zA-Z0-9_-]*$/;
export const DRIVER_SLUG_MAX_CHARS = 64;

export type DriverKind = string & { readonly __brand: 'DriverKind' };
export type BackendInstanceId = string & { readonly __brand: 'BackendInstanceId' };

function is_driver_slug(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length <= DRIVER_SLUG_MAX_CHARS &&
    DRIVER_SLUG_PATTERN.test(value)
  );
}
export function parse_driver_kind(value: unknown): DriverKind {
  if (!is_driver_slug(value))
    throw new PlannerError('validation', 'invalid driver kind', {
      driver_kind: typeof value === 'string' ? value : null,
    });
  return value as DriverKind;
}
export function parse_backend_instance_id(value: unknown): BackendInstanceId {
  if (!is_driver_slug(value))
    throw new PlannerError('validation', 'invalid backend instance id', {
      backend_key: typeof value === 'string' ? value : null,
    });
  return value as BackendInstanceId;
}
/** Driver-kind ids keep stored selections routable without migration. (T3 :106) */
export function default_instance_id_for_driver(kind: DriverKind): BackendInstanceId {
  return kind as string as BackendInstanceId;
}
/** Drivers decode their own config. Unknown-driver envelopes round-trip unchanged. */
export interface BackendInstanceConfig {
  readonly driver: DriverKind;
  readonly config?: unknown;
}
export type BackendInstanceConfigMap = ReadonlyMap<BackendInstanceId, BackendInstanceConfig>;
