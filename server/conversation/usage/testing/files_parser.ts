/** A scripted whole-file parser. Each file holds a JSON array of usage records. Its
 * fingerprint is its size, so a rewrite of the same length reads as unchanged. */
import { readFile } from 'node:fs/promises';
import type { FileTranscriptParser } from '../../../drivers/core/usage.ts';
import type { UsageRecord } from '../usageTranscripts.ts';

const scripted_files_parser: FileTranscriptParser = {
  kind: 'files',
  fingerprint: (_path, stat) => String(stat.size),
  read_file: async (path) => JSON.parse(await readFile(path, 'utf8')) as UsageRecord[],
};
export default scripted_files_parser;
