/** Where Claude Code's transcripts live, and the module that reads them. */
import type { TranscriptUsageSource } from '../../core/usage.ts';
import { transcript_home } from '../../../conversation/usage/transcriptHome.ts';

/** `home` is the configured Claude home as given, which may start with '~'. */
export function claude_transcript_usage(home: string): TranscriptUsageSource {
  return {
    kind: 'lines',
    home_directories: [transcript_home(home, 'projects')],
    parser_module: new URL('./transcript_parser.ts', import.meta.url).href,
  };
}
