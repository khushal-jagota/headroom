/** Participants share one source clock and the lifecycle owner's deadline. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
export interface SlotParticipant {
  start(): void;
  poll(database: Database, clock: Clock): void;
  stop(options: StopOptions): void | Promise<void>;
  settled(): Promise<void>;
}
