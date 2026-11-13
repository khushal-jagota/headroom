import type { Area } from '../core/authority/register.ts';
import type { ResourceProvider } from '../resource_versions/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';

/** One server owns updates, metrics readers, Ticket scratch, and environment routes. */
export interface EnvironmentRuntime {
  readonly areas: readonly Area[];
  readonly resourceProviders: readonly ResourceProvider[];
  before_worker_readiness_poll(): boolean;
  start(): void | Promise<void>;
  /** Fence producers immediately and retain pending starts and work until settlement. */
  close(options: StopOptions): void | Promise<void>;
  /** Actual starts, workers, readers and update/scratch work, not a timed observer. */
  settled(): Promise<void>;
}
