/** Cancelable maintenance and permanent shutdown share the command settlement owner. */
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { Quiescence, WorkingTurn } from '../../wire.generated.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type { MacCommandServiceContract } from './command_contracts.ts';
import type { MacRetirementDependencies } from './retirement_contracts.ts';

export interface MacMaintenanceDependencies extends MacRetirementDependencies {
  /** Composition resolves the cycle before admission opens. No placeholder command owner. */
  readonly commands: () => MacCommandServiceContract;
  readonly outbox: Pick<OutboxStore, 'pending_count'>;
  /** UUID hex source for Python's update_stop_ identity. */
  new_update_id(): string;
  on_failure(error: Error): void;
}
export interface MacMaintenanceContract {
  readonly permanently_stopped: boolean;
  /** Resource faults seal admission before the host starts its owned cleanup. */
  seal(): void;
  readonly planned_update_replay: boolean;
  working_turns(): readonly WorkingTurn[];
  quiesce(): Promise<Quiescence>;
  resume_after_cancelled_update(): void;
  begin_planned_replay(): void;
  planned_replay_finished(signal: AbortSignal): Promise<void>;
  finish_planned_replay(): void;
  prepare_update(mode: 'now' | 'idle'): Promise<boolean>;
  stop(options: StopOptions): Promise<void>;
  settled(): Promise<void>;
}
