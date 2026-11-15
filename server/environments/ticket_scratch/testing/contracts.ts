import type { WorkspaceState } from '../../../ticket_workspaces/contracts.ts';
import type { ScratchLayout } from '../contracts.ts';

export interface ScratchProbeRequest {
  root: string;
  home: string;
  database: string;
  now: number;
  states: [string, WorkspaceState][];
  tickets?: string;
  paths?: Record<string, string>;
  names?: string[];
  branches?: (string | null)[];
  gates?: { root: string | null; database: string | null; db_path: string }[];
  layout?: ScratchLayout;
  late_dirty?: string;
  later_clean?: string[];
  watch_paths?: string[];
  git_marker?: string;
}
