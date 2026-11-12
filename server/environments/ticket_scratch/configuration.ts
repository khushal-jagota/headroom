export const SCRATCH_FOLDERS = ['task-tmp', 'worktrees', 'repository-worktrees'] as const;
export const LEGACY_HOME_FOLDERS = ['pct', 'panels-session-evidence'] as const;
export const MERGE_TARGET = 'origin/staging';
export const SETTLE_SECONDS = 600;
export const CLEANUP_INTERVAL_SECONDS = 600;
export const STATUS_OPTIONS = ['--untracked-files=normal', '--ignore-submodules=none'] as const;
export const NOT_DESCENDED: ReadonlySet<string> = new Set(['.git', '.venv', 'node_modules']);
