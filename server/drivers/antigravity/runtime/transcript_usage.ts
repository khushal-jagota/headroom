/** Where Antigravity's conversation databases live, and the module that reads them. */
import type { TranscriptUsageSource } from '../../core/usage.ts';
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
