import type { VpsStatusPolicy } from './contracts.ts';
export const DEFAULT_VPS_STATUS_POLICY: VpsStatusPolicy = Object.freeze({
  disk_warning_free_ratio: 0.15,
  disk_warning_free_bytes: 10 * 1024 ** 3,
  disk_critical_free_ratio: 0.08,
  disk_critical_free_bytes: 5 * 1024 ** 3,
  backup_warning_age: 36 * 3600,
  backup_critical_age: 72 * 3600,
  log_warning_bytes: 25 * 1024 ** 2,
  temporary_expiry: 24 * 3600,
  cpu_warning_percent: 80,
  cpu_critical_percent: 95,
  ram_warning_percent: 80,
  ram_critical_percent: 95,
  disk_warning_used_percent: 85,
  disk_critical_used_percent: 92,
});
export const DIRECTORY_ENTRIES = 1024;
export const PROCESS_LINES = 128;
export const WORKTREE_ENTRIES = 128;
export const PROCESS_OUTPUT_CHARACTERS = 8192;
export const GIT_OUTPUT_CHARACTERS = 32768;
export const PROBE_TIMEOUT_MILLISECONDS = 1000;
