/** One agent backend kind, and the live instance it creates. (T3 provider-core driver.ts) */
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import type { BackendCapabilities } from './capabilities.ts';
import type { DriverClientDefinition } from './client.ts';
import type { DriverHost } from './host.ts';
import type { BackendInstanceId, DriverKind } from './identity.ts';
import type { DriverManifest } from './manifest.ts';

export interface DriverCreateInput<Config> {
  readonly instance_id: BackendInstanceId;
  /** Already parsed. */
  readonly config: Config;
}
/** create calls with a different instance_id or config share no mutable state. */
export interface AgentDriver<Config> {
  readonly driver_kind: DriverKind;
  /** Default true. Read by default_instance_configs. */
  readonly has_default_instance?: boolean;
  readonly client: DriverClientDefinition;
  readonly manifest: DriverManifest;
  readonly config: { parse(raw: unknown): Config; defaults(host: DriverHost): Config };
  /** A throw becomes an unavailable registry entry. */
  create(
    input: DriverCreateInput<Config>,
    host: DriverHost,
    signal: AbortSignal,
  ): Promise<DriverInstance>;
}
/** Each driver decodes its own config, so a list of drivers erases it. */
export type AnyAgentDriver = AgentDriver<any>;

export interface DriverInstance {
  readonly instance_id: BackendInstanceId;
  readonly driver_kind: DriverKind;
  readonly capabilities: BackendCapabilities;
  readonly child_factory: BackendChildFactory;
  /** Must not rely on `this`: callers may spread an instance. */
  dispose(): Promise<void>;
}
/** The config of a driver that takes none. */
export const NO_DRIVER_CONFIG: AgentDriver<null>['config'] = {
  parse: () => null,
  defaults: () => null,
};
