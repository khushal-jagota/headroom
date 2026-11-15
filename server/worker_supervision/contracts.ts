/** Process observations and cleanup evidence never grant authority from a PID alone. */
import type { LocalHostBootIdentity } from '../conversation/local_child_contracts.ts';

export interface WorkerProcess {
  readonly pid: number;
  readonly birth: string;
  readonly parent: number;
  readonly group: number;
  readonly session: number;
  readonly zombie: boolean;
}
export interface WorkerProcessPrimitives {
  readonly owns_isolated_session_members: boolean;
  readonly requires_containment: boolean;
  host_boot(deadline: number): Promise<LocalHostBootIdentity>;
  snapshot(deadline: number): Promise<ReadonlyMap<number, WorkerProcess>>;
  is_absent(process: WorkerProcess, deadline: number): Promise<boolean>;
  signal(process: WorkerProcess, signum: number, deadline: number): Promise<boolean>;
  /** Runs only in the isolated bootstrap, before backend execution. */
  prepare_launch(): void;
}
export interface CgroupIdentity {
  readonly path: string;
  readonly inode: bigint;
  readonly root_device: bigint;
  readonly root_inode: bigint;
}
export interface WorkerCgroupPrimitives {
  create(token: string): Promise<CgroupIdentity>;
  join(identity: CgroupIdentity): Promise<void>;
  kill(identity: CgroupIdentity): Promise<void>;
  empty(identity: CgroupIdentity): Promise<boolean>;
  remove(identity: CgroupIdentity): Promise<void>;
}
export interface CleanupResult {
  readonly complete: boolean;
  readonly reason: string;
  readonly scope: string;
}
export interface CleanupUncertainty {
  readonly ticket_id: string | null;
  readonly conversation_id: string | null;
  readonly record_path: string;
  readonly reason: string;
}
export interface ReconciliationRecord {
  readonly record_path: string;
  readonly ticket_id: string | null;
  readonly conversation_id: string | null;
  readonly result: CleanupResult;
}
export interface ReconciliationResult extends CleanupResult {
  readonly records: readonly ReconciliationRecord[];
}
export interface InspectionContext {
  readonly primitives: WorkerProcessPrimitives;
  readonly host: LocalHostBootIdentity;
}
export interface ProcessObservation {
  readonly stable: ReadonlyMap<number, WorkerProcess>;
  readonly children: ReadonlyMap<number, readonly WorkerProcess[]>;
  readonly sessions: ReadonlyMap<number, readonly WorkerProcess[]>;
  readonly latest_sessions: ReadonlyMap<number, readonly WorkerProcess[]>;
}
/** Optional fields retain readable historical v1 journals. Inspectors validate authority. */
export interface WorkerProcessRecord {
  readonly version: 1;
  readonly ticket_id: string;
  processes: WorkerProcess[];
  host?: string;
  boot?: string;
  owner?: WorkerProcess | null;
  root?: WorkerProcess | null;
  isolated?: boolean;
  state?:
    | 'pending'
    | 'spawned'
    | 'registered'
    | 'running'
    | 'bootstrap_failed'
    | 'not_proven'
    | 'complete';
  launch_phase?: 'pending' | 'registered' | 'running';
  conversation_id?: string | null;
  source_child_id?: string | null;
  source_incarnation?: string | null;
  reason?: string;
  launch_revoked?: boolean;
  boot_ended?: boolean;
  launch_failed?: boolean;
  cgroup?: CgroupIdentity;
  cgroup_joined?: boolean;
  cgroup_error?: string;
  observation_error?: string;
  unattributed_session_members?: WorkerProcess[];
}
