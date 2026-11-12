/** Production alone resolves ambient transcript homes. Test defaults remain synthetic. */
import { lstatSync, mkdirSync, readlinkSync } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { homedir } from 'node:os';
import type { Config } from '../../core/config.ts';
import { expand_path } from '../../environments/paths.ts';
import { backend_io_is_test } from '../snapshot/environment.ts';
import { UsageService } from './UsageService.ts';
import type { TranscriptDirectories, UsageServiceOptions } from './contracts.ts';
export interface UsageCompositionOptions extends Omit<UsageServiceOptions, 'now'> {
  readonly now?: () => Date;
  readonly transcriptDirs?: TranscriptDirectories;
  readonly stateDir?: string;
}
/** Path.resolve(strict=False) follows links before a later '..', and permits missing tails. */
function resolvedDatabasePath(path: string): string {
  function walk(value: string, base: string, active: Set<string>): string {
    let current = isAbsolute(value) ? '/' : base;
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') {
        current = dirname(current);
        continue;
      }
      const candidate = (current === '/' ? '' : current) + '/' + part;
      let entry;
      try {
        entry = lstatSync(candidate);
      } catch (error) {
        if (!(error instanceof Error && 'code' in error)) throw error;
        current = candidate;
        continue;
      }
      if (entry.isSymbolicLink()) {
        if (active.has(candidate))
          throw Object.assign(new Error('symbolic link loop'), { code: 'ELOOP' });
        active.add(candidate);
        try {
          current = walk(readlinkSync(candidate), current, active);
        } finally {
          active.delete(candidate);
        }
      } else current = candidate;
    }
    return current;
  }
  return walk(path, process.cwd(), new Set());
}
export function makeUsageService(
  config: Config,
  options: UsageCompositionOptions = {},
): UsageService {
  const parent = dirname(resolvedDatabasePath(config.db_path));
  const stateDir = options.stateDir ?? join(parent, 'usage');
  mkdirSync(stateDir, { recursive: true });
  const test = config.test_mode || backend_io_is_test();
  const transcriptDirs =
    options.transcriptDirs ??
    (test
      ? [
          {
            provider: 'codex' as const,
            dir: join(parent, 'usage-synthetic-homes', 'codex', 'sessions'),
          },
          {
            provider: 'claude' as const,
            dir: join(parent, 'usage-synthetic-homes', 'claude', 'projects'),
          },
        ]
      : [
          {
            provider: 'codex' as const,
            dir: join(expand_path(process.env.CODEX_HOME || join(homedir(), '.codex')), 'sessions'),
          },
          {
            provider: 'claude' as const,
            dir: join(
              expand_path(process.env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude')),
              'projects',
            ),
          },
        ]);
  return new UsageService(stateDir, transcriptDirs, {
    ratesFetcher: options.ratesFetcher ?? (test ? () => null : undefined),
    settingsProvider: options.settingsProvider,
    now: options.now === undefined ? undefined : () => options.now!().getTime(),
  });
}
