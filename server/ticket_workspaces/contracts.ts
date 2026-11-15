import type { OwnedOneShotChildren } from '../lifecycle/contracts.ts';

export const TICKET_ID_PATTERN = 't_[0-9abcdefghjkmnpqrstuvwxyz]{8}';
export const RETENTION_SECONDS = 7 * 24 * 60 * 60;
export const WORKTREE_DIRECTORY = 'worktree';
export const OWNERSHIP_FILE = '.panels-workspace.json';
export interface WorkspaceState {
  readonly ticket_id: string;
  readonly ended_at: number | null;
  readonly deleted: boolean;
}
export interface WorkspaceIdentity {
  readonly machine_id: string;
  readonly workspace_id: string;
}
export interface CleanupResult {
  removed: string[];
  skipped: { path: string; reason: string }[];
}
export type RetirementTarget = { path: string } | { reason: string };
export type MaybePromise<T> = T | Promise<T>;
export type WorkspaceRetirementOperation = (
  ticket_id: string,
  identity: WorkspaceIdentity,
  operation: () => MaybePromise<RetirementTarget>,
) => MaybePromise<RetirementTarget>;
export interface WorkspaceOptions {
  signal?: AbortSignal;
  admit_path?: (path: string) => string;
  git_execution?: {
    readonly children: OwnedOneShotChildren;
    readonly environment: Readonly<NodeJS.ProcessEnv>;
  };
  snapshot_ticket_scratch?: (ticket_id: string) => MaybePromise<readonly string[]>;
  prune_ticket_scratch?: (ticket_id: string, names: readonly string[]) => MaybePromise<boolean>;
}
export interface WorkspaceCleanupOptions extends WorkspaceOptions {
  extra_roots?: readonly string[];
  active_ticket_ids?: ReadonlySet<string>;
  apply?: boolean;
  before_remove?: (path: string) => MaybePromise<string | null>;
  authorize_retirement?: (
    state: WorkspaceState,
    identity: WorkspaceIdentity,
  ) => MaybePromise<boolean>;
  retire?: (
    path: string,
    state: WorkspaceState,
    identity: WorkspaceIdentity,
  ) => MaybePromise<RetirementTarget>;
  retirement_is_committed?: (
    state: WorkspaceState,
    identity: WorkspaceIdentity,
  ) => MaybePromise<boolean>;
}
export interface RetirementOptions extends WorkspaceOptions {
  retired_root?: string;
  identity?: WorkspaceIdentity;
}
export interface WorkspaceMarker extends WorkspaceIdentity {
  ticket_id: string;
  repository?: string | null;
  worktree?: string;
  branch?: string | null;
  preparing?: boolean;
  initial_commit?: string | null;
}
export interface RetirementRecord extends WorkspaceState, WorkspaceIdentity {
  source: string;
  target: string;
  scratch_names: readonly string[];
}
