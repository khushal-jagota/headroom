/** Test registries and drivers over scripted factories. Each factory object is kept unchanged. */
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import type { AnyAgentDriver } from '../core/driver.ts';
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { DriverHost } from '../core/host.ts';
import { parse_backend_instance_id, parse_driver_kind } from '../core/identity.ts';
import { driver_registry_from_entries } from '../core/registry.ts';
import type { DriverRegistry, RegistryEntry } from '../core/registry.ts';

const CAPABILITIES = {
  sessions: { supportsModelSwitchInSession: true },
  turns: { supportsActiveSteering: true },
} as const;
function scripted_driver(
  key: string,
  child_factory: (host: DriverHost) => BackendChildFactory,
): AnyAgentDriver {
  const driver_kind = parse_driver_kind(key);
  return {
    driver_kind,
    client: {
      label: key,
      icon: { file: 'icon.svg', monochrome: true },
      accent_color: '#000000',
    },
    manifest: { executable: { name: key } },
    config: NO_DRIVER_CONFIG,
    create: async ({ instance_id }, host) => ({
      instance_id,
      driver_kind,
      capabilities: CAPABILITIES,
      child_factory: child_factory(host),
      dispose: async () => {},
    }),
  };
}
/** Drivers whose create returns the factory each binding gives for the host. */
export function drivers_of_factories(
  bindings: Readonly<Record<string, (host: DriverHost) => BackendChildFactory>>,
): AnyAgentDriver[] {
  return Object.entries(bindings).map(([key, binding]) => scripted_driver(key, binding));
}
/** For compositions that receive a host and must never read it. */
export const unread_driver_host = new Proxy(
  {},
  {
    get(_target, property) {
      throw new Error(`This test composition reads no driver host (${String(property)})`);
    },
  },
) as DriverHost;
export function registry_of_factories(
  factories: Readonly<Record<string, BackendChildFactory>>,
): DriverRegistry {
  const entries = Object.entries(factories).map(([key, child_factory]): RegistryEntry => {
    const driver = scripted_driver(key, () => child_factory);
    const driver_kind = driver.driver_kind;
    return {
      kind: 'live',
      driver,
      instance: {
        instance_id: parse_backend_instance_id(key),
        driver_kind,
        capabilities: CAPABILITIES,
        child_factory,
        dispose: async () => {},
      },
    };
  });
  return driver_registry_from_entries(
    entries.map((entry) => entry.driver!),
    entries,
  );
}
