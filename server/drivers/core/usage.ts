/** Token usage read from a driver's own transcripts. The usage worker cannot receive functions,
 * so a driver names a parser module, which the worker imports. */
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
/** A file a whole-file parser lists. Its cached read is reused while its fingerprint holds. */
export interface ListedTranscriptFile {
  readonly path: string;
  readonly fingerprint: string;
  /** The file's latest change, which the scan cache prunes by. */
  readonly mtimeMs: number;
}
/** One file's records and the JSON state kept with them in the scan cache. */
export interface FileTranscriptRead<State> {
  readonly records: readonly UsageRecord[];
  readonly state: State;
}
/** Lists its own files and reads a whole file again whenever its fingerprint changes. */
export interface FileTranscriptParser<State> {
  readonly kind: 'files';
  /** The files under `root` that may hold records at or after `since_ms`. */
  list_files(root: string, since_ms: number): Promise<readonly ListedTranscriptFile[]>;
  read_file(path: string): Promise<FileTranscriptRead<State>>;
  /** The state kept in the scan cache. Undefined: the cached state is not this parser's. */
  revive_state(raw: unknown): State | undefined;
  /** Each read file's final records, in order. A file's records may depend on the other files. */
  finish(files: readonly FileTranscriptRead<State>[]): readonly (readonly UsageRecord[])[];
}
