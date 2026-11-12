import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile, rename, unlink, readFile } from 'node:fs/promises';
import { existsSync, realpathSync, lstatSync, statSync } from 'node:fs';
import { dirname, join, resolve, sep } from 'node:path';
import type { ConversationMessageFiles, KeptMessageFile } from './message_files.ts';
export class MessageFileMissing extends Error {}

export class FileConversationMessageFiles implements ConversationMessageFiles {
    private readonly root: string;
    constructor(db_path: string) {
        this.root = join(dirname(resolve(db_path)), 'files', 'conversations');
    }
    async keep(conversation_id: string, contents: Uint8Array, options: {
        media_type: string;
        sender_message_id?: string | null;
        piece_index?: number;
    }): Promise<KeptMessageFile> {
        const bytes = Buffer.from(contents);
        const identity = JSON.stringify([conversation_id, options.sender_message_id, options.piece_index ?? 0,
            options.media_type, createHash('sha256').update(bytes).digest('hex')])
            .replace(/[\u007f-\uffff]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
        const stored_file_id = 'f_' + (options.sender_message_id == null
            ? randomUUID().replaceAll('-', '') : createHash('sha256').update(identity).digest('hex'));
        const folder = join(this.root, conversation_id);
        await mkdir(folder, { recursive: true });
        const target = join(folder, stored_file_id);
        const temporary = join(folder, '.' + randomUUID().replaceAll('-', ''));
        try {
            await writeFile(temporary, bytes);
            await rename(temporary, target);
            await writeFile(temporary, options.media_type, 'utf8');
            await rename(temporary, target + '.media-type');
        }
        finally {
            await unlink(temporary).catch(error => {
                if (error.code !== 'ENOENT')
                    throw error;
            });
        }
        return {
            conversation_id, stored_file_id, media_type: options.media_type,
            absolute_path: target, byte_count: bytes.length
        };
    }
    path_of(conversation_id: string, stored_file_id: string): string {
        const folder = resolve(this.root, conversation_id);
        const target = resolve(folder, stored_file_id);
        if (!/^[A-Za-z0-9_-]+$/.test(conversation_id) || !/^[A-Za-z0-9_-]+$/.test(stored_file_id)
            || !existsSync(this.root) || lstatSync(this.root).isSymbolicLink()
            || !existsSync(folder) || lstatSync(folder).isSymbolicLink()
            || !folder.startsWith(resolve(this.root) + sep) || !target.startsWith(folder + sep)
            || !existsSync(target) || !statSync(target).isFile() || !realpathSync(target).startsWith(realpathSync(folder) + sep)) {
            throw new MessageFileMissing(stored_file_id);
        }
        return target;
    }
    async read(conversation_id: string, stored_file_id: string): Promise<Uint8Array> {
        return readFile(this.path_of(conversation_id, stored_file_id));
    }
    async media_type_of(conversation_id: string, stored_file_id: string): Promise<string | null> {
        const target = this.path_of(conversation_id, stored_file_id);
        try {
            return (await readFile(target + '.media-type', 'utf8')).trim() || null;
        }
        catch {
            return null;
        }
    }
}
