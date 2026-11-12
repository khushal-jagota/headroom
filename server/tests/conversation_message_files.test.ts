import {
  mkdtempSync,
  openSync,
  closeSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  unlinkSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import {
  ConversationMessageFiles,
  MessageFileMissing,
} from '../conversation/record/message_files.ts';

// SQL and codec proofs cannot establish byte retention, Python-compatible retry IDs,
// sidecar reads, or replacement without truncation of an already-open attachment.
test('a kept attachment survives retry and atomic replacement with its media type', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'panels-message-file-'));
  const files = new ConversationMessageFiles(join(directory, 'record.db'));
  const contents = Buffer.from('complete attachment\n');
  const options = { media_type: 'text/plain', sender_message_id: 'sender-雪-😀', piece_index: 2 };
  try {
    const kept = await files.keep('conversation', contents, options);
    // Independently calculated with Python's json.dumps and hashlib.sha256.
    expect(kept.stored_file_id).toBe(
      'f_f09008bf40cd0f53f02a4d3fa7985907a59341aebbbe8421f7069b10f4a0e8ae',
    );
    expect(kept.byte_count).toBe(contents.byteLength);
    expect(Buffer.from(await files.read('conversation', kept.stored_file_id))).toEqual(contents);
    expect(await files.media_type_of('conversation', kept.stored_file_id)).toBe('text/plain');
    const path = files.path_of('conversation', kept.stored_file_id);
    const original_inode = statSync(path).ino;
    const descriptor = openSync(path, 'r');
    try {
      const retry = await files.keep('conversation', contents, options);
      expect(retry).toEqual(kept);
      expect(statSync(path).ino).not.toBe(original_inode);
      expect(readFileSync(descriptor)).toEqual(contents);
    } finally {
      closeSync(descriptor);
    }
    expect(readdirSync(join(directory, 'files', 'conversations', 'conversation')).sort()).toEqual([
      kept.stored_file_id,
      kept.stored_file_id + '.media-type',
    ]);
    unlinkSync(path + '.media-type');
    expect(await files.media_type_of('conversation', kept.stored_file_id)).toBeNull();
    unlinkSync(path);
    await expect(files.read('conversation', kept.stored_file_id)).rejects.toBeInstanceOf(
      MessageFileMissing,
    );
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
