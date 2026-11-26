/** Where Antigravity's conversation databases live, and the module that reads them. */
import { homedir } from 'node:os';
import type { DriverHost } from '../../core/host.ts';
import { home_path } from '../../core/paths.ts';
import type { TranscriptUsageSource } from '../../core/usage.ts';
import { antigravity_manifest } from '../manifest.ts';
import { transcript_home } from '../../../conversation/usage/transcriptHome.ts';
import { AGY_APP_DATA_DIRECTORY_NAME } from './configuration.ts';

/** `home` is the configured Antigravity home as given, which may start with '~'. agy keeps one
 * SQLite file per conversation, the CLI's and Panels' alike, in its app data directory. */
export function antigravity_transcript_usage(home: string): TranscriptUsageSource {
  return {
    kind: 'files',
    home_directories: [transcript_home(home, `${AGY_APP_DATA_DIRECTORY_NAME}/conversations`)],
    parser_module: new URL('./transcript_parser.ts', import.meta.url).href,
  };
}

/** The transcripts under the home `environment` configures, as an instance resolves it. */
export function antigravity_configured_transcript_usage(
  environment: DriverHost['environment'],
): TranscriptUsageSource {
  return antigravity_transcript_usage(
    home_path(antigravity_manifest.home!, environment, homedir(), {
      unset: '||',
      expand_configured: false,
    }),
  );
}
