/** Required production handoffs. Scripted proofs supply the same explicit dependencies. */
import type { TestCompositionContext } from '../core/start.ts';
import type { MacUpdateServiceContract } from './updates/mac/contracts.ts';
import type { EnvironmentOperations } from './runtime.ts';

export interface ExecutorUpdateBindings {
  configured(): boolean;
  health_payload(): Record<string, unknown> | null;
  bind_updates(service: MacUpdateServiceContract): void;
}
export interface ProductionEnvironmentDependencies {
  /** Null is genuine absence, valid only when config.executor_id is null. */
  readonly executor: ExecutorUpdateBindings | null;
  readonly application_root: string;
  readonly node_executable: string;
  /** Explicit packaging admission fact. Null admits no legacy-null runtime. */
  readonly admitted_legacy_null_sha: string | null;
}
/** Production requires this explicit adapter. There is no successful missing adapter. */
export type ProductionEnvironmentFactory = (
  context: TestCompositionContext,
  dependencies: ProductionEnvironmentDependencies,
) => Promise<EnvironmentOperations>;
