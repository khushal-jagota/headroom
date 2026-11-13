/** The complete local runner owns command, native and source work. Storage stays with its host. */
import type {
  BackendEventSink,
  BackendEventValue,
} from '../../../conversation/backends/contracts.ts';
import type { BackendEvent, ChildReference, TurnToken } from '../../wire.generated.ts';
import type { MailboxRunner } from '../mailbox/contracts.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type {
  MacChildStateContract,
  MacCommandDependencies,
  MacOutputPublication,
} from './command_contracts.ts';
import type {
  MacNativeChildrenContract,
  MacNativeChildrenOptions,
  MacRunningChild,
} from './contracts.ts';
import type { MacSourceWorkContract } from './retirement_contracts.ts';
import type {
  MacMaintenanceContract,
  MacMaintenanceDependencies,
} from './maintenance_contracts.ts';
import type { MacRunnerLifetimeContract } from './lifetime_contracts.ts';

export interface MacLiveEvents {
  /** Lossy and nonblocking. This is a port, not an HTTP sender. */
  send(event: BackendEvent): void;
}
export interface MacEventsDependencies {
  readonly native_children: MacNativeChildrenContract;
  readonly state: MacChildStateContract;
  readonly sources: MacSourceWorkContract;
  readonly publication: MacOutputPublication;
  readonly live: MacLiveEvents | null;
}
export interface MacRunnerEventsContract {
  event_sink(reference: ChildReference): BackendEventSink;
  report_event(
    reference: ChildReference,
    turn: TurnToken | null,
    event: BackendEventValue,
  ): Promise<void>;
}
export interface MacRunnerOptions {
  readonly incarnation: string;
  readonly native: Omit<MacNativeChildrenOptions, 'event_sink'>;
  readonly journal: MacCommandDependencies['journal'];
  readonly outbox: OutboxStore;
  readonly inputs: MacCommandDependencies['inputs'];
  readonly live: MacLiveEvents | null;
  readonly now_ms: MacCommandDependencies['now_ms'];
  readonly new_update_id: MacMaintenanceDependencies['new_update_id'];
}
export interface MacRunnerContract
  extends
    MailboxRunner,
    MacMaintenanceContract,
    MacRunnerEventsContract,
    MacRunnerLifetimeContract {
  readonly native_children: MacNativeChildrenContract;
  seal_admission(): void;
  authenticate(child_id: string | null, capability: string | null): MacRunningChild;
  withdraw(command_id: string): boolean;
  source_work<T>(
    reference: ChildReference,
    work: () => Promise<T>,
    options?: { final_event?: boolean },
  ): Promise<T>;
}
