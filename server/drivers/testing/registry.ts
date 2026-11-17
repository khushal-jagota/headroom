/** Test registries and drivers over scripted factories. Each factory object is kept unchanged. */
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import { KNOWN_BACKEND_INSTANCE_IDS } from '../built_in_manifests.ts';
import type { AnyAgentDriver } from '../core/driver.ts';
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { DriverHost } from '../core/host.ts';
import { parse_backend_instance_id, parse_driver_kind } from '../core/identity.ts';
import { turn_scoped_selection_transition } from '../core/selection.ts';
import type { DriverManifest } from '../core/manifest.ts';
import { driver_registry_from_entries } from '../core/registry.ts';
import type { DriverRegistry, RegistryEntry } from '../core/registry.ts';
import type { DriverStatus } from '../core/status.ts';

const CAPABILITIES = {
  sessions: { supportsModelSwitchInSession: true },
  turns: { supportsActiveSteering: true },
} as const;
/** A manifest with only what a scripted driver needs. */
export function scripted_manifest(name: string): DriverManifest {
  return {
    executable: { name },
    launcher_environment_names: [],
    status_recipe: {
      executable_name: name,
      missing_binary_diagnosis: `\`${name}\` is not installed.`,
      identity_arguments: null,
      login_command: null,
      registry_package_name: null,
      native_path_marker: null,
      native_update_command: null,
      manual_only_detail: `Panels offers no update for \`${name}\`.`,
    },
  };
}
/** A status whose card always says the executable is missing. */
export function scripted_status(name: string): DriverStatus {
  return {
    version_detects_upgrade: false,
    probe: async () => ({
      installed: false,
      executable_path: null,
      version: null,
      identity: null,
      available_models: [],
      reasoning_effort_options: [],
      default_model_id: null,
      default_reasoning_effort: null,
      update_advisory: null,
      diagnoses: [`\`${name}\` is not installed.`],
      models_complete: false,
    }),
  };
}
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
    manifest: scripted_manifest(key),
    config: NO_DRIVER_CONFIG,
    create: async ({ instance_id }, host) => ({
      instance_id,
      driver_kind,
      capabilities: CAPABILITIES,
      plan_selection_transition: turn_scoped_selection_transition,
      child_factory: child_factory(host),
      status: scripted_status(key),
      dispose: async () => {},
    }),
  };
}
/** Built-in ids first, in BUILT_IN_DRIVERS order, so cards and choice lists keep their order. */
function in_built_in_order<T>(entries: readonly [string, T][]): [string, T][] {
  const rank = (key: string) => {
    const index = (KNOWN_BACKEND_INSTANCE_IDS as readonly string[]).indexOf(key);
    return index === -1 ? KNOWN_BACKEND_INSTANCE_IDS.length : index;
  };
  return [...entries].sort(([left], [right]) => rank(left) - rank(right));
}
/** Drivers whose create returns the factory each binding gives for the host. */
export function drivers_of_factories(
  bindings: Readonly<Record<string, (host: DriverHost) => BackendChildFactory>>,
): AnyAgentDriver[] {
  return in_built_in_order(Object.entries(bindings)).map(([key, binding]) =>
    scripted_driver(key, binding),
  );
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
  const entries = in_built_in_order(Object.entries(factories)).map(
    ([key, child_factory]): RegistryEntry => {
      const driver = scripted_driver(key, () => child_factory);
      const driver_kind = driver.driver_kind;
      return {
        kind: 'live',
        driver,
        instance: {
          instance_id: parse_backend_instance_id(key),
          driver_kind,
          capabilities: CAPABILITIES,
          plan_selection_transition: turn_scoped_selection_transition,
          child_factory,
          status: scripted_status(key),
          dispose: async () => {},
        },
      };
    },
  );
  return driver_registry_from_entries(
    entries.map((entry) => entry.driver!),
    entries,
  );
}
