/** Production reads the transcript homes it is given. Tests read synthetic homes instead. */
import { mkdirSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import type { Config } from '../../core/config.ts';
import { resolve_path_sync } from '../../core/path_resolution.ts';
import { backend_io_is_test } from '../snapshot/environment.ts';
import { UsageService } from './UsageService.ts';
import type {
  TranscriptDirectories,
  TranscriptDirectory,
  UsageServiceOptions,
  UsageTranscriptSource,
} from './contracts.ts';
export interface UsageCompositionOptions extends Omit<UsageServiceOptions, 'now'> {
  readonly now?: () => Date;
  readonly transcriptDirs?: TranscriptDirectories;
  /** Each backend instance's transcripts. Absent: no transcripts are read. */
  readonly transcriptSources?: readonly UsageTranscriptSource[];
  readonly stateDir?: string;
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
    (options.transcriptSources ?? []).flatMap(({ provider, source }) =>
      source.home_directories.map((dir): TranscriptDirectory => ({
        provider,
        dir: test ? join(parent, 'usage-synthetic-homes', provider, basename(dir)) : dir,
        kind: source.kind,
        parser_module: source.parser_module,
      })),
    );
  return new UsageService(stateDir, transcriptDirs, {
    ratesFetcher: options.ratesFetcher ?? (test ? () => null : undefined),
    settingsProvider: options.settingsProvider,
    now: options.now === undefined ? undefined : () => options.now!().getTime(),
  });
}
