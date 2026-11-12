/** Required production handoffs. Scripted proofs supply the same explicit dependencies. */
import type { TestCompositionContext } from '../core/start.ts';
import type { EnvironmentOperations } from './runtime.ts';

export interface ExecutorUpdateBindings {
  configured(): boolean;
  health_payload(): Record<string, unknown> | null;
}
export interface ProductionEnvironmentDependencies {
  /** Null is genuine absence, valid only when config.executor_id is null. */
  readonly executor: ExecutorUpdateBindings | null;
  readonly application_root: string;
  readonly node_executable: string;
}
/** Implemented by environments/production.ts. There is no successful missing adapter. */
export type ProductionEnvironmentFactory = (
  context: TestCompositionContext,
  dependencies: ProductionEnvironmentDependencies,
) => Promise<EnvironmentOperations>;
