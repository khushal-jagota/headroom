/** Type-only boundary for the existing Ticket scratch policy. */
import type { ReadSql } from '../../core/database/contracts.ts';
import type { ComposedBackgroundService, OwnedOneShotChildren } from '../../lifecycle/contracts.ts';

export interface ScratchLayout {
  readonly repository: string;
  readonly folders: readonly string[];
}
export interface Worktree {
  readonly path: string;
  readonly branch: string | null;
}
export interface TicketScratch {
  readonly ticket_id: string;
  readonly entries: readonly string[];
  readonly branches: readonly string[];
}
export type TicketStates = ReadonlyMap<string, readonly [stage: string, updated_at: number]>;
export interface ScratchResult {
  removed: string[];
  deleted_branches: string[];
  kept_branches: string[];
  skipped: { path: string; reason: string }[];
}

/** One pass can refuse further operation dispatch after lifecycle shutdown. */
export interface ScratchPassOptions {
  signal?: AbortSignal;
  git_execution?: {
    readonly children: OwnedOneShotChildren;
    readonly environment: Readonly<NodeJS.ProcessEnv>;
  };
}
export interface GitResult {
  returncode: number;
  stdout: string;
  stderr: string;
}
export interface TicketScratchOperations {
  layout_under(root: string, home?: string): Promise<ScratchLayout>;
  layout_for(
    root: string | null,
    database: string | null,
    db_path: string,
  ): Promise<ScratchLayout | null>;
  read_ticket_states(sql: ReadSql): TicketStates;
  read_ticket_states_from(db_path: string): Promise<TicketStates>;
  collect_ticket_scratch(
    layout: ScratchLayout,
    states: TicketStates,
    now: number,
    options?: ScratchPassOptions,
  ): Promise<TicketScratch[]>;
  remove_ticket_scratch(
    layout: ScratchLayout,
    scratches: Iterable<TicketScratch>,
    states: TicketStates,
    now: number,
    options?: ScratchPassOptions,
  ): Promise<ScratchResult>;
  clean_finished_ticket_scratch(
    layout: ScratchLayout,
    db_path: string,
    now: number,
    already_kept: Set<string>,
    options?: ScratchPassOptions,
  ): Promise<ScratchResult>;
}

export interface ScratchRuntimeOptions {
  readonly layout: ScratchLayout | null;
  readonly db_path: string;
  readonly test_mode: boolean;
  now_unix(): number;
  assert_database_owned(): void;
  /** Composition supplies the real pass. A held pass also proves lifecycle fencing. */
  clean_finished_ticket_scratch: TicketScratchOperations['clean_finished_ticket_scratch'];
}
export type ScratchRuntime = ComposedBackgroundService;
