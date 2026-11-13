import { readFile, stat } from 'node:fs/promises';
import { dirname } from 'node:path';
import { applicationRootFromEnvironment } from '../../core/config.ts';
import { JsonNumber } from '../../core/http/numeric_json.ts';
import {
  expand_path,
  is_directory,
  is_symlink,
  resolved_path,
  verified_snapshots,
} from '../backup/evidence.ts';
import { collect_cleanup_inventory, candidate_body } from './cleanup.ts';
import {
  DEFAULT_VPS_STATUS_POLICY,
  GIT_OUTPUT_CHARACTERS,
  PROCESS_LINES,
  WORKTREE_ENTRIES,
} from './configuration.ts';
import { bounded_children, characters, DEFAULT_DEPENDENCIES, safe_under } from './probes.ts';
import {
  decimal_integer,
  instant,
  overall_state,
  recognised_panels_workload_role,
  round_percent,
  utc_iso,
} from './logic/status.ts';
import type {
  StatusConfig,
  StatusOptions,
  StatusSection,
  StatusState,
  VpsStatusDependencies,
  VpsStatusPolicy,
  VpsStatusSnapshot,
} from './contracts.ts';
export async function resolve_status_application_root(
  environment: Record<string, string | undefined> = process.env,
  home?: string,
  admit_path?: (path: string) => string,
): Promise<string> {
  const path = expand_path(applicationRootFromEnvironment(environment), home);
  return resolved_path(admit_path?.(path) ?? path, home);
}
export async function backup_section(
  root: string,
  now: string,
  policy: VpsStatusPolicy,
): Promise<StatusSection> {
  if (!(await is_directory(root)) || (await is_symlink(root)))
    return {
      state: 'unavailable',
      summary: 'backup directory is unavailable',
      verified_snapshot_count: 0,
      latest_verified_at: null,
    };
  let snapshots: readonly string[];
  try {
    snapshots = await verified_snapshots(root);
  } catch {
    return {
      state: 'unavailable',
      summary: 'verified backup evidence could not be inspected',
      verified_snapshot_count: 0,
      latest_verified_at: null,
    };
  }
  if (!snapshots.length)
    return {
      state: 'critical',
      summary: 'no verified backup snapshot',
      verified_snapshot_count: 0,
      latest_verified_at: null,
    };
  let created: string, age: number;
  try {
    const metadata: unknown = JSON.parse(await readFile(snapshots[0] + '/metadata.json', 'utf8'));
    if (
      metadata === null ||
      typeof metadata !== 'object' ||
      !('created_at' in metadata) ||
      typeof metadata.created_at !== 'string'
    )
      throw new Error('missing backup timestamp');
    created = utc_iso(metadata.created_at);
    age = Number(instant(now) - instant(created)) / 1_000_000;
    if (age < 0) throw new Error('verified backup timestamp is in the future');
  } catch {
    return {
      state: 'review_needed',
      summary: 'verified backup age is unavailable',
      verified_snapshot_count: snapshots.length,
      latest_verified_at: null,
    };
  }
  return {
    state:
      age >= policy.backup_critical_age
        ? 'critical'
        : age >= policy.backup_warning_age
          ? 'warning'
          : 'healthy',
    summary: 'latest verified backup',
    verified_snapshot_count: snapshots.length,
    latest_verified_at: created,
    age_seconds: Math.trunc(age),
  };
}
function exact_integer(value: bigint): number | JsonNumber {
  return value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= BigInt(Number.MIN_SAFE_INTEGER)
    ? Number(value)
    : new JsonNumber(String(value));
}
async function disk_section(
  root: string,
  dependencies: VpsStatusDependencies,
  policy: VpsStatusPolicy,
): Promise<StatusSection> {
  const unavailable = {
    state: 'unavailable' as const,
    summary: 'runtime disk path is unavailable',
    free_bytes: null,
    total_bytes: null,
    free_percent: null,
  };
  if (!(await is_directory(root))) return unavailable;
  let total: bigint, free: bigint;
  try {
    const stats = await dependencies.statvfs(root);
    total = BigInt(stats.f_frsize) * BigInt(stats.f_blocks);
    free = BigInt(stats.f_frsize) * BigInt(stats.f_bavail);
  } catch {
    return { ...unavailable, summary: 'runtime disk probe failed' };
  }
  if (total <= 0)
    return {
      state: 'review_needed',
      summary: 'runtime disk capacity is invalid',
      free_bytes: exact_integer(free),
      total_bytes: exact_integer(total),
      free_percent: null,
    };
  const ratio = Number(free) / Number(total);
  let state: StatusState = 'healthy';
  if (ratio < policy.disk_critical_free_ratio || free < BigInt(policy.disk_critical_free_bytes))
    state = 'critical';
  else if (ratio < policy.disk_warning_free_ratio || free < BigInt(policy.disk_warning_free_bytes))
    state = 'warning';
  return {
    state,
    summary: 'runtime disk capacity',
    free_bytes: exact_integer(free),
    total_bytes: exact_integer(total),
    free_percent: round_percent(ratio * 100),
  };
}
async function workloads_section(dependencies: VpsStatusDependencies): Promise<StatusSection> {
  let lines: readonly string[];
  try {
    lines = (await dependencies.process_lines()).slice(0, PROCESS_LINES);
  } catch {
    return { state: 'unavailable', summary: 'process probe failed', items: [] };
  }
  const items: unknown[] = [];
  for (const line of lines) {
    const fields = /^\s*(\S+)\s+(\S+)\s+(\S+)\s+(.*\S)\s*$/.exec(line);
    if (!fields || !/^\p{Decimal_Number}+$/u.test(fields[1]) || Array.from(fields[3]).length > 32)
      continue;
    const role = recognised_panels_workload_role(fields[4]);
    if (role !== null)
      items.push({
        role,
        pid: exact_integer(decimal_integer(fields[1])),
        state: 'running',
        age: fields[3],
      });
  }
  return { state: 'healthy', summary: 'recognised Panels workloads', items };
}
async function worktrees_section(
  root: string,
  dependencies: VpsStatusDependencies,
): Promise<StatusSection> {
  if (!(await is_directory(root)))
    return { state: 'unavailable', summary: 'repository worktree proof is unavailable', items: [] };
  let output: string;
  try {
    output = await dependencies.git_worktree_output(root);
  } catch {
    return { state: 'unavailable', summary: 'git worktree probe failed', items: [] };
  }
  const items: { state: string }[] = [];
  for (const block of characters(output, GIT_OUTPUT_CHARACTERS)
    .split('\n\n')
    .slice(0, WORKTREE_ENTRIES)) {
    const first = block.split(/\r?\n/)[0];
    if (first.startsWith('worktree ') && (await is_directory(first.slice('worktree '.length))))
      items.push({ state: 'proven' });
  }
  return { state: 'healthy', summary: 'git-proven worktrees', count: items.length, items };
}
async function logs_section(root: string, policy: VpsStatusPolicy): Promise<StatusSection> {
  if (!(await is_directory(root)) || (await is_symlink(root)))
    return {
      state: 'unavailable',
      summary: 'log directory is unavailable',
      file_count: 0,
      total_bytes: null,
    };
  let total = 0,
    count = 0;
  try {
    for (const path of await bounded_children(root)) {
      if (!(await safe_under(root, path, false))) continue;
      total += (await stat(path)).size;
      count++;
    }
  } catch {
    return {
      state: 'review_needed',
      summary: 'log directory could not be fully inspected',
      file_count: count,
      total_bytes: total,
    };
  }
  return {
    state: total >= policy.log_warning_bytes ? 'warning' : 'healthy',
    summary: 'configured log usage',
    file_count: count,
    total_bytes: total,
  };
}
export async function collect_vps_status(
  config: StatusConfig,
  options: StatusOptions = {},
): Promise<VpsStatusSnapshot> {
  const dependencies = { ...DEFAULT_DEPENDENCIES, ...options.dependencies };
  const policy = options.policy ?? DEFAULT_VPS_STATUS_POLICY;
  const now = utc_iso(dependencies.now());
  const runtimeRoot = dirname(await resolved_path(config.db_path));
  const backupRoot = expand_path(config.backup_dir),
    logsRoot = expand_path(config.logs_dir);
  const inventory = await collect_cleanup_inventory(config, { dependencies, policy });
  const environment: StatusSection = (await is_directory(runtimeRoot))
    ? { state: 'healthy', summary: 'runtime path is available' }
    : { state: 'unavailable', summary: 'runtime path is unavailable' };
  const app: StatusSection =
    config.app_sha === null
      ? { state: 'review_needed', summary: 'app identity is not configured', sha: null }
      : { state: 'healthy', summary: 'configured app identity', sha: config.app_sha };
  const backup = await backup_section(backupRoot, now, policy);
  const disk = await disk_section(runtimeRoot, dependencies, policy);
  const workloads = await workloads_section(dependencies);
  const worktrees = await worktrees_section(
    options.application_root || (await resolve_status_application_root()),
    dependencies,
  );
  const logs = await logs_section(logsRoot, policy);
  const cleanup_candidates: StatusSection = {
    state: inventory.candidates.length ? 'warning' : 'healthy',
    summary: inventory.candidates.length
      ? 'maintenance candidates need review'
      : 'no eligible cleanup',
    candidates: inventory.candidates.map(candidate_body),
  };
  const resources: StatusSection = {
    state: 'unavailable',
    summary:
      dependencies.platform_name() === 'Darwin'
        ? 'resource collection is unavailable on macOS'
        : 'Linux resource collection is deferred',
    cpu_percent: null,
    load_averages: null,
    ram: null,
    swap: null,
  };
  return {
    collected_at: now,
    overall_state: overall_state(
      [
        environment,
        app,
        backup,
        disk,
        workloads,
        worktrees,
        logs,
        cleanup_candidates,
        resources,
      ].map((section) => section.state),
    ),
    environment,
    app,
    backup,
    disk,
    workloads,
    worktrees,
    logs,
    cleanup_candidates,
    resources,
  };
}
