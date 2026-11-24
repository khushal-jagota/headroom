/** One agent backend kind, and the live instance it creates. (T3 provider-core driver.ts) */
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import type { BackendUsageAdapter } from '../../conversation/backend_usage/contracts.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
import type { BackendCapabilities } from './capabilities.ts';
import type { DriverClientDefinition } from './client.ts';
import type { ExecutorDriver } from './executor.ts';
import type { DriverHost } from './host.ts';
import type { BackendInstanceId, DriverKind } from './identity.ts';
import type { DriverManifest } from './manifest.ts';
import type { SelectionTransitionInput, SelectionTransitionPlan } from './selection.ts';
import type { DriverSignInController } from './sign_in.ts';
import type { DriverStatus } from './status.ts';
import type { TranscriptUsageSource } from './usage.ts';

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
  readonly capabilities: BackendCapabilities;
  plan_selection_transition(input: SelectionTransitionInput): SelectionTransitionPlan;
  /** The file this agent reads from its start folder for standing instructions. */
  readonly home_instructions_file: string;
  readonly config: { parse(raw: unknown): Config; defaults(host: DriverHost): Config };
  /** A throw becomes an unavailable registry entry. */
  create(
    input: DriverCreateInput<Config>,
    host: DriverHost,
    signal: AbortSignal,
  ): Promise<DriverInstance>;
  /** Loads the half an executor machine runs. */
  executor(): Promise<ExecutorDriver>;
  /** Pure rules over model ids. On the driver, so they hold while an instance is unavailable. */
  readonly models?: DriverModelRules;
  /** Why this driver cannot launch the model, or null. */
  launch_model_refusal?(model: string): string | null;
  /** Readable form of a stored tool-call detail, or null when it holds nothing readable. Absent: as stored. */
  readable_tool_call_detail?(detail: string): string | null;
}
export interface DriverModelRules {
  /** The model family a card shows as selected_variant, or null. */
  variant(model_id: string): string | null;
  /** The numerical version this driver reads from its own model ids. Empty: none. */
  version(model_id: string): readonly number[];
  /** Whether a requested model is among the offered ones. Absent: exact match. */
  is_offered?(requested: string, offered: readonly string[]): boolean;
  /** A readable name for a model id its catalog does not label well, or null. Absent: none. */
  readable_name?(model_id: string): string | null;
}
/** Each driver decodes its own config, so a list of drivers erases it. */
export type AnyAgentDriver = AgentDriver<any>;

export interface DriverInstance {
  readonly instance_id: BackendInstanceId;
  readonly driver_kind: DriverKind;
  readonly capabilities: BackendCapabilities;
  readonly child_factory: BackendChildFactory;
  readonly status: DriverStatus;
  readonly allowance_usage?: BackendUsageAdapter;
  readonly transcript_usage?: TranscriptUsageSource;
  readonly sign_in?: DriverSignInController;
  /** Every local child reports a supervision record before `start()` resolves. */
  readonly supervises_local_launches?: boolean;
  /** How a running child takes a new model or effort. Must not use `this`: callers spread instances. */
  plan_selection_transition(input: SelectionTransitionInput): SelectionTransitionPlan;
  /** Must not rely on `this`: callers may spread an instance. May be called again. */
  dispose(options?: StopOptions): Promise<void>;
}
/** The config of a driver that takes none. */
export const NO_DRIVER_CONFIG: AgentDriver<null>['config'] = {
  parse: () => null,
  defaults: () => null,
};
