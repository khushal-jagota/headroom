/** Participants share one source clock and the lifecycle owner's deadline. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
export interface SlotParticipant {
  start(): void;
  poll(database: Database, clock: Clock): void;
  stop(options: { deadline?: number }): void | Promise<void>;
  settled(): Promise<void>;
}
