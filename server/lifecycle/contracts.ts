import type { Database } from '../core/database/contracts.ts';
import type { Config } from '../core/config.ts';
import type { Clock } from '../core/clock.ts';
import type { ChangeSignal } from '../core/changes/index.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';

/** The same absolute monotonic deadline is supplied to every loop. */
export interface StopOptions {
  deadline: number;
}
export interface ComposedBackgroundService {
  start(): void | Promise<void>;
  close(options: StopOptions): void | Promise<void>;
}
export interface BackgroundCompositionContext {
  database: Database;
  config: Config;
  clock: Clock;
  changes: ChangeSignal;
  conversationSystem?: ConversationSystem;
  assert_database_owned(): void;
}
export type BackgroundServiceFactory = (
  context: BackgroundCompositionContext,
) => ComposedBackgroundService;
export interface RunningServer {
  close(): Promise<void>;
}
/** The retained advisory lease shared by environment operators and the future supervisor. */
export interface LifecycleLease {
  close(): void;
}
export class ServerLifecycleError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ServerLifecycleError';
  }
}
export class ServerLifecycleAlreadyOwnedError extends ServerLifecycleError {
  constructor(message: string) {
    super(message);
    this.name = 'ServerLifecycleAlreadyOwnedError';
  }
}
