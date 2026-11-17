/** Token usage read from a driver's own transcripts. The usage worker cannot receive functions,
 * so a driver names a parser module, which the worker imports. */
import type { Stats } from 'node:fs';
import type { UsageRecord } from '../../conversation/usage/usageTranscripts.ts';

export interface TranscriptUsageSource {
  readonly kind: 'lines' | 'files';
  /** Where the transcripts live, already resolved for this instance. */
  readonly home_directories: readonly string[];
  /** An absolute file URL. Its default export is a LineTranscriptParser or FileTranscriptParser. */
  readonly parser_module: string;
}
/** Reads appended lines, carrying state from one line to the next and across scans. */
export interface LineTranscriptParser<State> {
  readonly kind: 'lines';
  initial_state(): State;
  /** The state kept in the scan cache. Undefined: the cached state is not this parser's. */
  revive_state(raw: unknown): State | undefined;
  /** A cheap test that skips lines which cannot carry usage. */
  might_carry_usage(line: string): boolean;
  parse_line(line: string, state: State): readonly UsageRecord[];
}
/** Reads a whole file again whenever its fingerprint changes. */
export interface FileTranscriptParser {
  readonly kind: 'files';
  fingerprint(path: string, stat: Stats): string;
  read_file(path: string): Promise<readonly UsageRecord[]>;
}
