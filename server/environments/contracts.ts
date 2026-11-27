import type { FastifyPluginAsync } from 'fastify';
import type { Area } from '../core/authority/register.ts';
import type { ServerMoveNotice } from '../executor/wire.generated.ts';
import type { ResourceProvider } from '../resource_versions/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { UpdateBarrier } from './update_barrier.ts';
import type { MacUpdateServiceContract } from './updates/mac/contracts.ts';
import type { StreamCloseSink } from '../core/http/live_streams/records.ts';

/** One server owns updates, metrics readers, Ticket scratch, and environment routes. */
export interface EnvironmentRuntime {
  /** Scripted hosts can omit environment services. The real composition requires this instance. */
  readonly mac_updates?: MacUpdateServiceContract;
  readonly update_barrier?: UpdateBarrier;
  /** Startup and bounded retry probe; holds the installer lock during orphan recovery. */
  recover_update_barrier?(): void | Promise<void>;
  update_attempt_id?(): string;
  /** Only an admitted waiting release can supply a prefetched screen snapshot. */
  waiting_screens_root?(): string | null;
  reopen_update_work?(attempt_id: string): void;
  readonly areas: readonly Area[];
  /** Executor routes with their own machine admission, outside the ordinary area door. */
  readonly executor_routes?: readonly FastifyPluginAsync[];
  /** The server move notice for one machine's lease reply, or null. */
  server_move_notice?(machine_id: string): ServerMoveNotice | null;
  readonly resourceProviders: readonly ResourceProvider[];
  /** Where each live stream close is stored. Dropped records never disturb a stream. */
  readonly stream_close_sink?: StreamCloseSink;
  before_worker_readiness_poll(): boolean;
  start(): void | Promise<void>;
  /** Fence producers immediately and retain pending starts and work until settlement. */
  close(options: StopOptions): void | Promise<void>;
  /** Actual starts, workers, readers and update/scratch work, not a timed observer. */
  settled(): Promise<void>;
}
