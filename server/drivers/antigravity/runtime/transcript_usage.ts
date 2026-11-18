/** Where Antigravity's conversation databases live, and the module that reads them. */
import type { TranscriptUsageSource } from '../../core/usage.ts';
import { transcript_home } from '../../../conversation/usage/transcriptHome.ts';

/** `home` is the configured Antigravity home as given, which may start with '~'. Both the CLI and
 * the ACP adapter keep one SQLite file per conversation under their own directory. */
export function antigravity_transcript_usage(home: string): TranscriptUsageSource {
  return {
    kind: 'files',
    home_directories: [
      transcript_home(home, 'antigravity-cli/conversations'),
      transcript_home(home, 'antigravity-acp/conversations'),
    ],
    parser_module: new URL('./transcript_parser.ts', import.meta.url).href,
  };
}
