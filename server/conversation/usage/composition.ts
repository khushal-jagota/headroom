/** Production alone resolves ambient transcript homes. Test defaults remain synthetic. */
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';
import type { Config } from '../../core/config.ts';
import { resolve_path_sync } from '../../core/path_resolution.ts';
import { expand_path } from '../../environments/paths.ts';
import { backend_io_is_test } from '../snapshot/environment.ts';
import { UsageService } from './UsageService.ts';
import type { TranscriptDirectories, UsageServiceOptions } from './contracts.ts';
export interface UsageCompositionOptions extends Omit<UsageServiceOptions, 'now'> {
  readonly now?: () => Date;
  readonly transcriptDirs?: TranscriptDirectories;
  readonly stateDir?: string;
}
/** pathlib removes empty and '.' segments, but deliberately retains '..'. */
export function transcript_home(path: string, child: string): string {
  const expanded = expand_path(path);
  const root =
    expanded.startsWith('//') && !expanded.startsWith('///')
      ? '//'
      : expanded.startsWith('/')
        ? '/'
        : '';
  const parts = expanded.split('/').filter((part) => part !== '' && part !== '.');
  return root + [...parts, child].join('/');
}
export function makeUsageService(
  config: Config,
  options: UsageCompositionOptions = {},
): UsageService {
  const parent = dirname(resolve_path_sync(config.db_path));
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
            dir: transcript_home(process.env.CODEX_HOME || join(homedir(), '.codex'), 'sessions'),
          },
          {
            provider: 'claude' as const,
            dir: transcript_home(
              process.env.CLAUDE_CONFIG_DIR || join(homedir(), '.claude'),
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
