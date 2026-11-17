/** The live backend instances, built once from the driver list. (T3 ProviderInstanceRegistry buildEntry) */
import { PlannerError } from '../../core/contracts.ts';
import type { AnyAgentDriver, DriverInstance } from './driver.ts';
import type { DriverHost } from './host.ts';
import { default_instance_id_for_driver } from './identity.ts';
import type {
  BackendInstanceConfig,
  BackendInstanceConfigMap,
  BackendInstanceId,
  DriverKind,
} from './identity.ts';

export type RegistryEntry =
  | { readonly kind: 'live'; readonly driver: AnyAgentDriver; readonly instance: DriverInstance }
  | {
      readonly kind: 'unavailable';
      readonly instance_id: BackendInstanceId;
      readonly driver: AnyAgentDriver | null;
      readonly reason: string;
    };

export interface DriverRegistry {
  readonly drivers: ReadonlyMap<DriverKind, AnyAgentDriver>;
  get(id: string): RegistryEntry | undefined;
  /** Throws PlannerError('validation','unknown agent backend',{backend_key, backend_keys}). */
  require_live(id: string): DriverInstance;
  /** Build order, which is the driver list order. */
  list(): readonly RegistryEntry[];
  dispose(): Promise<void>;
}

function message_of(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
export function registry_entry_id(entry: RegistryEntry): BackendInstanceId {
  return entry.kind === 'live' ? entry.instance.instance_id : entry.instance_id;
}
/** One entry per driver that has a default instance. There is no stored instance config yet. */
export function default_instance_configs(
  drivers: readonly AnyAgentDriver[],
): BackendInstanceConfigMap {
  return new Map(
    drivers
      .filter((driver) => driver.has_default_instance !== false)
      .map((driver) => [
        default_instance_id_for_driver(driver.driver_kind),
        { driver: driver.driver_kind },
      ]),
  );
}
function drivers_by_kind(drivers: readonly AnyAgentDriver[]): Map<DriverKind, AnyAgentDriver> {
  const by_kind = new Map<DriverKind, AnyAgentDriver>();
  for (const driver of drivers) {
    if (by_kind.has(driver.driver_kind))
      throw new Error(`Driver '${driver.driver_kind}' is listed twice`);
    by_kind.set(driver.driver_kind, driver);
  }
  return by_kind;
}
/** A registry over entries that already exist. */
export function driver_registry_from_entries(
  drivers: readonly AnyAgentDriver[],
  entries: readonly RegistryEntry[],
): DriverRegistry {
  const by_kind = drivers_by_kind(drivers);
  const by_id = new Map<string, RegistryEntry>();
  for (const entry of entries) {
    const id = registry_entry_id(entry);
    if (by_id.has(id)) throw new Error(`Backend '${id}' has two registry entries`);
    by_id.set(id, entry);
  }
  const listed = [...entries];
  return {
    drivers: by_kind,
    get: (id) => by_id.get(id),
    require_live(id) {
      const entry = by_id.get(id);
      if (entry?.kind === 'live') return entry.instance;
      throw new PlannerError('validation', 'unknown agent backend', {
        backend_key: id,
        backend_keys: listed.flatMap((item) =>
          item.kind === 'live' ? [item.instance.instance_id] : [],
        ),
        ...(entry === undefined ? {} : { reason: entry.reason }),
      });
    },
    list: () => listed,
    async dispose() {
      const failures: unknown[] = [];
      for (const entry of listed) {
        if (entry.kind !== 'live') continue;
        try {
          await entry.instance.dispose();
        } catch (error) {
          failures.push(error);
        }
      }
      if (failures.length) throw new AggregateError(failures, 'Backend instances did not dispose');
    },
  };
}
async function build_entry(
  drivers: ReadonlyMap<DriverKind, AnyAgentDriver>,
  instance_id: BackendInstanceId,
  entry: BackendInstanceConfig,
  host: DriverHost,
  signal: AbortSignal,
): Promise<RegistryEntry> {
  const driver = drivers.get(entry.driver);
  if (driver === undefined)
    return {
      kind: 'unavailable',
      instance_id,
      driver: null,
      reason: `Driver '${entry.driver}' is not registered in this build.`,
    };
  let config: unknown;
  try {
    config = driver.config.parse(entry.config ?? driver.config.defaults(host));
  } catch (error) {
    return {
      kind: 'unavailable',
      instance_id,
      driver,
      reason: `Invalid config for backend '${instance_id}': ${message_of(error)}`,
    };
  }
  try {
    const instance = await driver.create({ instance_id, config }, host, signal);
    return { kind: 'live', driver, instance };
  } catch (error) {
    return {
      kind: 'unavailable',
      instance_id,
      driver,
      reason: `Backend '${instance_id}' could not start: ${message_of(error)}`,
    };
  }
}
/** Builds one entry at a time, in config order. A bad install becomes an unavailable entry. */
export async function create_driver_registry(options: {
  readonly drivers: readonly AnyAgentDriver[];
  readonly configs: BackendInstanceConfigMap;
  readonly host: DriverHost;
  readonly signal: AbortSignal;
}): Promise<DriverRegistry> {
  const by_kind = drivers_by_kind(options.drivers);
  const entries: RegistryEntry[] = [];
  for (const [instance_id, entry] of options.configs)
    entries.push(await build_entry(by_kind, instance_id, entry, options.host, options.signal));
  return driver_registry_from_entries(options.drivers, entries);
}
