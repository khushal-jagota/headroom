import type { Config } from '../../core/config.ts';
export type StatusState = 'healthy' | 'warning' | 'critical' | 'unavailable' | 'review_needed';
export interface VpsStatusPolicy {
  disk_warning_free_ratio: number;
  disk_warning_free_bytes: number;
  disk_critical_free_ratio: number;
  disk_critical_free_bytes: number;
  backup_warning_age: number;
  backup_critical_age: number;
  log_warning_bytes: number;
  temporary_expiry: number;
  cpu_warning_percent: number;
  cpu_critical_percent: number;
  ram_warning_percent: number;
  ram_critical_percent: number;
  disk_warning_used_percent: number;
  disk_critical_used_percent: number;
}
export interface DiskStat {
  f_frsize: number | bigint;
  f_blocks: number | bigint;
  f_bavail: number | bigint;
}
export interface VpsStatusDependencies {
  now(): string;
  platform_name(): string;
  statvfs(path: string): Promise<DiskStat>;
  process_lines(): Promise<readonly string[]>;
  git_worktree_output(root: string): Promise<string>;
  live_reference_paths(): Promise<readonly string[]>;
}
export type StatusConfig = Pick<Config, 'db_path' | 'backup_dir' | 'logs_dir' | 'app_sha'>;
export interface StatusSection {
  state: StatusState;
  summary: string;
  [key: string]: unknown;
}
export interface VpsStatusSnapshot {
  collected_at: string;
  overall_state: StatusState;
  environment: StatusSection;
  app: StatusSection;
  backup: StatusSection;
  disk: StatusSection;
  workloads: StatusSection;
  worktrees: StatusSection;
  logs: StatusSection;
  cleanup_candidates: StatusSection;
  resources: StatusSection;
}
export interface CleanupCandidate {
  kind: 'log_rotation' | 'backup_retention' | 'temporary_backup';
  path: string;
  root: string;
  expires_at: string | null;
}
export interface CleanupInventory {
  candidates: readonly CleanupCandidate[];
  policy: VpsStatusPolicy;
  dependencies: VpsStatusDependencies;
}
export interface CleanupResult {
  removed: string[];
  rotated: string[];
  review_needed: string[];
}
export interface StatusOptions {
  dependencies?: Partial<VpsStatusDependencies>;
  policy?: VpsStatusPolicy;
  application_root?: string;
}
