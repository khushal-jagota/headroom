/** A scripted whole-file parser. Each `.json` file holds a JSON array of usage records. Its
 * fingerprint is its size, so a rewrite of the same length reads as unchanged. */
import { readFile } from 'node:fs/promises';
import type { FileTranscriptParser } from '../../../drivers/core/usage.ts';
import { listTranscriptFiles } from '../usageTranscriptReader.ts';
import type { UsageRecord } from '../usageTranscripts.ts';

const scripted_files_parser: FileTranscriptParser<null> = {
  kind: 'files',
  list_files: async (root, since_ms) =>
    (await listTranscriptFiles(root, since_ms, { extension: '.json' })).map((file) => ({
      path: file.path,
      fingerprint: String(file.size),
      mtimeMs: file.mtimeMs,
    })),
  read_file: async (path) => ({
    records: JSON.parse(await readFile(path, 'utf8')) as UsageRecord[],
    state: null,
  }),
  revive_state: (raw) => (raw === null ? null : undefined),
  finish: (files) => files.map((file) => file.records),
};
export default scripted_files_parser;
