/** The complete local runner owns command, native and source work. Storage stays with its host. */
import type {
  BackendEventSink,
  BackendEventValue,
} from '../../../conversation/backends/contracts.ts';
import type {
  BackendEvent,
  ChildCommand,
  ChildReference,
  CommandOutcome,
  TurnToken,
  WorkerCleanupUncertainty,
} from '../../wire.generated.ts';
import type { CleanupReconciliationDependencies } from '../cleanup_contracts.ts';
import type { WorkspaceIdentity, WorkspaceOptions, WorkspaceState } from '../../../ticket_workspaces/contracts.ts';
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
  readonly native: Omit<MacNativeChildrenOptions,
    'event_sink' | 'workspace_lock' | 'workspace_git' | 'register_workspace' | 'worker_cleanup'>;
  readonly workspace_git: NonNullable<WorkspaceOptions['git_execution']>;
  readonly register_workspace: (child: ChildReference, workspace: WorkspaceIdentity) => Promise<void>;
  readonly retire_workspace: (state: WorkspaceState, workspace: WorkspaceIdentity) => Promise<boolean>;
  readonly retirement_is_committed: (state: WorkspaceState, workspace: WorkspaceIdentity) => Promise<boolean>;
  readonly cleanup: CleanupReconciliationDependencies;
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
  cleanup_workspaces(states: readonly WorkspaceState[], now: number): Promise<void>;
  readonly cleanup_uncertainties: WorkerCleanupUncertainty[];
  readonly legacy_cleanup_notice: string | null;
  refresh_worker_cleanup(options?: { ticket_id?: string; revoke_pending?: boolean }): Promise<void>;
  /** Unjournaled exact stop with durable FIFO publication before custody release.
   * Concurrent calls for the same source join its settled outcome or receive a refusal.
   * Publication failures can retry the same ID. Permanent runner shutdown refuses new calls. */
  stop_and_publish(command: ChildCommand): Promise<CommandOutcome>;
  seal_admission(): void;
  authenticate(child_id: string | null, capability: string | null): MacRunningChild;
  withdraw(command_id: string): boolean;
  source_work<T>(
    reference: ChildReference,
    work: () => Promise<T>,
    options?: { final_event?: boolean },
  ): Promise<T>;
}
