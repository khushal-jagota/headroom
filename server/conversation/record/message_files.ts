/** Durable message bytes beside the conversation record, with Python-compatible IDs. */
import { createHash, randomUUID } from 'node:crypto';
import { lstatSync, realpathSync, statSync } from 'node:fs';
import { mkdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative } from 'node:path';
import type {
  ConversationMessageFiles as MessageFilesContract,
  KeptMessageFile,
} from '../message_files.ts';

export const STORED_FILE_ID_PREFIX = 'f_';
export const MEDIA_TYPE_SUFFIX = '.media-type';

export class MessageFileMissing extends Error {
  constructor(stored_file_id: string, options?: ErrorOptions) {
    super(stored_file_id, options);
    this.name = 'MessageFileMissing';
  }
}

export function new_stored_file_id(): string {
  return STORED_FILE_ID_PREFIX + randomUUID().replaceAll('-', '');
}

function sha256(bytes: Uint8Array | string): string {
  return createHash('sha256').update(bytes).digest('hex');
}

/** Python json.dumps(ensure_ascii=True, separators=(',', ':')). */
function ascii_identity(values: readonly (string | number)[]): string {
  return JSON.stringify(values).replace(
    /[\u007f-\uffff]/g,
    (character) => '\\u' + character.charCodeAt(0).toString(16).padStart(4, '0'),
  );
}

function inside(root: string, target: string): boolean {
  const path = relative(root, target);
  return path !== '..' && !path.startsWith('../') && !isAbsolute(path);
}

export class ConversationMessageFiles implements MessageFilesContract {
  readonly #root: string;

  constructor(db_path: string) {
    this.#root = join(dirname(db_path), 'files', 'conversations');
  }

  async keep(
    conversation_id: string,
    contents: Uint8Array,
    options: {
      media_type: string;
      sender_message_id?: string | null;
      piece_index?: number;
    },
  ): Promise<KeptMessageFile> {
    const { media_type, sender_message_id = null, piece_index = 0 } = options;
    const folder = join(this.#root, conversation_id);
    await mkdir(folder, { recursive: true });
    const stored_file_id =
      sender_message_id === null
        ? new_stored_file_id()
        : STORED_FILE_ID_PREFIX +
          sha256(
            ascii_identity([
              conversation_id,
              sender_message_id,
              piece_index,
              media_type,
              sha256(contents),
            ]),
          );
    const target = join(folder, stored_file_id);
    const temporary = join(folder, '.' + randomUUID().replaceAll('-', ''));
    try {
      // A retry must never truncate a file that another accepted message can read.
      await writeFile(temporary, contents);
      await rename(temporary, target);
      await writeFile(temporary, media_type, 'utf8');
      await rename(temporary, join(folder, stored_file_id + MEDIA_TYPE_SUFFIX));
    } finally {
      try {
        await unlink(temporary);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      }
    }
    return {
      conversation_id,
      stored_file_id,
      media_type,
      absolute_path: target,
      byte_count: contents.byteLength,
    };
  }

  async read(conversation_id: string, stored_file_id: string): Promise<Uint8Array> {
    return readFile(this.path_of(conversation_id, stored_file_id));
  }

  async media_type_of(conversation_id: string, stored_file_id: string): Promise<string | null> {
    const beside = join(
      dirname(this.path_of(conversation_id, stored_file_id)),
      stored_file_id + MEDIA_TYPE_SUFFIX,
    );
    let bytes: Uint8Array;
    try {
      bytes = await readFile(beside);
    } catch (error) {
      if (!(error instanceof Error) || !('code' in error)) throw error;
      return null;
    }
    // Match Python's strict UTF-8 read and str.strip(), including ASCII separators.
    const stated = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
      .decode(bytes)
      .replace(
        /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,
        '',
      );
    return stated || null;
  }

  path_of(conversation_id: string, stored_file_id: string): string {
    try {
      if (!/^[A-Za-z0-9_-]+$/.test(conversation_id) || !/^[A-Za-z0-9_-]+$/.test(stored_file_id)) {
        throw new Error('unsafe managed file id');
      }
      if (lstatSync(this.#root).isSymbolicLink()) {
        throw new Error('managed file root is a symlink');
      }
      const root = realpathSync(this.#root);
      const entity = join(root, conversation_id);
      if (lstatSync(entity).isSymbolicLink()) {
        throw new Error('managed entity directory is a symlink');
      }
      const entity_root = realpathSync(entity);
      const target = realpathSync(join(entity_root, stored_file_id));
      if (!inside(root, entity_root) || !inside(entity_root, target)) {
        throw new Error('managed file escapes managed root');
      }
      if (!statSync(target).isFile()) {
        throw new Error('managed file is not a regular file');
      }
      return target;
    } catch (error) {
      throw new MessageFileMissing(stored_file_id, { cause: error });
    }
  }
}
