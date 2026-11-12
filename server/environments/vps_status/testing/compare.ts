/** Export-only synthetic status and filesystem proof. No host probe runs here. */
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { PassThrough } from 'node:stream';
import {
  lstat,
  readdir,
  readFile,
  readlink,
  rm,
  symlink,
  utimes,
  writeFile,
} from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { resolved_path } from '../../paths.ts';
import {
  is_verified_snapshot,
  verified_snapshots,
  verified_snapshot_retention_plan,
} from '../../backup/evidence.ts';
import { backup_section, collect_vps_status } from '../collect.ts';
import { apply_cleanup_inventory, collect_cleanup_inventory, inventory_body } from '../cleanup.ts';
import { DEFAULT_VPS_STATUS_POLICY } from '../configuration.ts';
import type { StatusConfig, VpsStatusDependencies, VpsStatusPolicy } from '../contracts.ts';
interface Mutation {
  kind: 'touch' | 'symlink' | 'write' | 'remove';
  path: string;
  target?: string;
  text?: string;
  time?: number;
}
interface Probe {
  root: string;
  now: string;
  operation: 'status' | 'cleanup' | 'evidence';
  platform?: string;
  policy?: Partial<VpsStatusPolicy>;
  processes?: string[];
  git?: string;
  disk?: { f_frsize: number; f_blocks: number; f_bavail: number };
  references?: string[];
  mutations?: Mutation[];
  denied_database?: string;
  paths?: string[];
}
async function answer(work: () => unknown): Promise<unknown> {
  try {
    return { result: await work(), error: null };
  } catch (error) {
    if (error instanceof Error && error.name === 'RuntimeError')
      return { result: null, error: error.message };
    if (!(error instanceof Error) || !('code' in error)) throw error;
    return {
      result: null,
      error: error.code === 'ERR_ENCODING_INVALID_ENCODED_DATA' ? 'decode' : 'io',
    };
  }
}
async function tree(root: string): Promise<Record<string, unknown>> {
  const result: Record<string, unknown> = {};
  async function walk(path: string, prefix = ''): Promise<void> {
    for (const name of await readdir(path)) {
      const child = join(path, name),
        key = prefix + name;
      const entry = await lstat(child);
      if (entry.isSymbolicLink()) result[key] = { link: await readlink(child) };
      else if (entry.isDirectory()) {
        result[key] = { directory: true };
        await walk(child, key + '/');
      } else
        result[key] = {
          sha256: createHash('sha256')
            .update(await readFile(child))
            .digest('hex'),
        };
    }
  }
  await walk(root);
  return result;
}
export async function probe_status(cases: readonly Probe[]): Promise<unknown[]> {
  const answers: unknown[] = [];
  for (const input of cases) {
    const config: StatusConfig = {
      db_path: join(input.root, 'runtime/database.sqlite'),
      backup_dir: join(input.root, 'backups'),
      logs_dir: join(input.root, 'logs'),
      app_sha: 'a'.repeat(40),
    };
    const dependencies: VpsStatusDependencies = {
      now: () => input.now,
      platform_name: () => input.platform ?? 'Darwin',
      statvfs: async () =>
        input.disk ?? { f_frsize: 4096, f_blocks: 100_000_000, f_bavail: 60_000_000 },
      process_lines: async () => input.processes ?? [],
      git_worktree_output: async () => input.git ?? '',
      live_reference_paths: async () => input.references ?? [],
    };
    const policy = { ...DEFAULT_VPS_STATUS_POLICY, ...input.policy };
    const original = fs.createReadStream;
    if (input.denied_database) {
      fs.createReadStream = ((path: fs.PathLike, ...arguments_: unknown[]) => {
        if (String(path) !== input.denied_database) return original(path, arguments_[0] as never);
        const stream = new PassThrough();
        process.nextTick(() =>
          stream.destroy(
            Object.assign(new Error('synthetic denied backup read'), { code: 'EACCES' }),
          ),
        );
        return stream;
      }) as typeof original;
      syncBuiltinESMExports();
    }
    try {
      if (input.operation === 'status') {
        answers.push(
          await collect_vps_status(config, {
            dependencies,
            policy,
            application_root: join(input.root, 'app'),
          }),
        );
      } else if (input.operation === 'cleanup') {
        const inventory = await collect_cleanup_inventory(config, { dependencies, policy });
        for (const mutation of input.mutations ?? []) {
          if (mutation.kind === 'touch')
            await utimes(mutation.path, mutation.time!, mutation.time!);
          else if (mutation.kind === 'symlink') {
            await rm(mutation.path, { recursive: true });
            await symlink(mutation.target!, mutation.path);
          } else if (mutation.kind === 'remove') await rm(mutation.path, { recursive: true });
          else await writeFile(mutation.path, mutation.text ?? '');
        }
        answers.push({
          inventory: inventory_body(inventory),
          applied: await apply_cleanup_inventory(inventory),
          tree: await tree(input.root),
        });
      } else {
        const candidates = (await readdir(config.backup_dir))
          .filter((name) => name.startsWith('snapshot-'))
          .sort();
        const evidence: Record<string, unknown> = {};
        for (const name of candidates)
          evidence[name] = await answer(() => is_verified_snapshot(join(config.backup_dir, name)));
        answers.push({
          evidence,
          resolved_paths: await Promise.all(
            (input.paths ?? []).map((path) => answer(() => resolved_path(path))),
          ),
          snapshots: await answer(() => verified_snapshots(config.backup_dir)),
          retention: await answer(() => verified_snapshot_retention_plan(config.backup_dir)),
          backup: await backup_section(config.backup_dir, input.now, policy),
          collection: await answer(() =>
            collect_vps_status(config, {
              dependencies,
              policy,
              application_root: join(input.root, 'app'),
            }),
          ),
        });
      }
    } finally {
      fs.createReadStream = original;
      syncBuiltinESMExports();
    }
  }
  return answers;
}
