/** Where Claude Code's transcripts live, and the module that reads them. */
import { homedir } from 'node:os';
import type { DriverHost } from '../../core/host.ts';
import { home_path } from '../../core/paths.ts';
import type { TranscriptUsageSource } from '../../core/usage.ts';
import { claude_manifest } from '../manifest.ts';
import { transcript_home } from '../../../conversation/usage/transcriptHome.ts';

/** `home` is the configured Claude home as given, which may start with '~'. */
export function claude_transcript_usage(home: string): TranscriptUsageSource {
  return {
    kind: 'lines',
    home_directories: [transcript_home(home, 'projects')],
    parser_module: new URL('./transcript_parser.ts', import.meta.url).href,
  };
}

/** The transcripts under the home `environment` configures, as an instance resolves it. */
export function claude_configured_transcript_usage(
  environment: DriverHost['environment'],
): TranscriptUsageSource {
  return claude_transcript_usage(
    home_path(claude_manifest.home!, environment, homedir(), {
      unset: '||',
      expand_configured: false,
    }),
  );
}
