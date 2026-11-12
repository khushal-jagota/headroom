/** Route-required durable admission and atomic adoption. No upload intake or cleanup loop. */
import { randomUUID } from 'node:crypto';
import { mkdirSync, renameSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Database, ReadSql } from '../../core/database/contracts.ts';
import type { KeptMessageFile } from '../message_files.ts';
import { MEDIA_TYPE_SUFFIX } from '../record/message_files.ts';
import { destination_from_json, destination_to_json, VideoUploadError } from './contracts.ts';
import type {
  CheckedVideoUpload,
  UploadDestination,
  UploadState,
  VideoUploadOutcome,
  VideoUploadStatus,
} from './contracts.ts';
import { validate_binding } from './logic/rules.ts';

const UNSAFE_IDENTIFIER_CHARACTER = /[^A-Za-z0-9_-]/;
interface UploadRow {
  upload_id: string;
  destination_json: string;
  filename: string;
  byte_count: number;
  received_bytes: number;
  state: UploadState;
  media_type: string | null;
  expires_at: number;
  error: string | null;
  stored_file_id: string;
  sender_message_id: string | null;
  conversation_id: string | null;
}
function file_stat(path: string): ReturnType<typeof statSync> | null {
  try {
    return statSync(path);
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      ['ENOENT', 'ENOTDIR'].includes(String(error.code))
    )
      return null;
    throw error;
  }
}
function remove_if_present(path: string): void {
  try {
    unlinkSync(path);
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
}
export class VideoUploadService {
  readonly database: Database;
  readonly db_path: string;
  readonly root: string;

  constructor(database: Database, db_path: string) {
    this.database = database;
    this.db_path = db_path;
    this.root = join(dirname(db_path), 'files', 'video-uploads');
    mkdirSync(this.root, { recursive: true });
  }

  private row(sql: ReadSql, upload_id: string): UploadRow {
    const row = sql.get<UploadRow>('SELECT * FROM conversation_video_uploads WHERE upload_id=?', [
      upload_id,
    ]);
    if (!row) throw new VideoUploadError('Video upload not found.', 404);
    return row;
  }

  private temporary(upload_id: string): string {
    if (!upload_id || UNSAFE_IDENTIFIER_CHARACTER.test(upload_id))
      throw new VideoUploadError('Invalid upload identifier.');
    return join(this.root, upload_id);
  }

  private path(row: UploadRow): string {
    if (row.conversation_id !== null)
      return join(
        dirname(this.db_path),
        'files',
        'conversations',
        row.conversation_id,
        row.stored_file_id,
      );
    return this.temporary(row.upload_id);
  }

  private checked_row(sql: ReadSql, checked: CheckedVideoUpload): UploadRow {
    const row = this.row(sql, checked.upload_id);
    if (
      row.sender_message_id !== checked.sender_message_id ||
      row.destination_json !== destination_to_json(checked.destination)
    ) {
      throw new VideoUploadError('Video admission does not match this message.', 409);
    }
    return row;
  }

  status(upload_id: string): VideoUploadStatus {
    return this.database.write((tx) => {
      const row = this.row(tx, upload_id);
      return {
        upload_id: row.upload_id,
        destination: destination_from_json(row.destination_json),
        filename: row.filename,
        byte_count: row.byte_count,
        received_bytes: row.received_bytes,
        state: row.state,
        media_type: row.media_type,
        expires_at: row.expires_at,
        error: row.error,
      };
    });
  }

  preflight(
    upload_id: string,
    destination: UploadDestination,
    sender_message_id: string,
  ): CheckedVideoUpload {
    if (!sender_message_id)
      throw new VideoUploadError('Video messages require a stable message identity.');
    return this.database.write((tx) => {
      const row = this.row(tx, upload_id);
      const stored = destination_from_json(row.destination_json);
      const same_destination = stored.kind === destination.kind && stored.id === destination.id;
      const retry =
        row.sender_message_id === sender_message_id &&
        same_destination &&
        ['admitted', 'linked'].includes(row.state);
      if (!retry || row.conversation_id === null) validate_binding(stored, destination);
      if (!retry && row.sender_message_id !== null && row.sender_message_id !== sender_message_id) {
        throw new VideoUploadError('This video already belongs to another message.', 409);
      }
      if (!['ready', 'admitted', 'linked'].includes(row.state))
        throw new VideoUploadError('The video is not ready. Retry its upload.', 409);
      if (row.state === 'ready' && row.expires_at <= Math.floor(Date.now() / 1000)) {
        throw new VideoUploadError('The video expired. Attach it again.', 409);
      }
      if (row.state !== 'linked') {
        tx.run(
          "UPDATE conversation_video_uploads SET state='admitted',sender_message_id=? WHERE upload_id=?",
          [sender_message_id, upload_id],
        );
      }
      return {
        upload_id,
        destination: stored,
        sender_message_id,
        conversation_id: row.conversation_id,
      };
    });
  }

  adopt(checked: CheckedVideoUpload, conversation_id: string): KeptMessageFile {
    if (!conversation_id || UNSAFE_IDENTIFIER_CHARACTER.test(conversation_id))
      throw new VideoUploadError('Invalid conversation identifier.');
    // Record the target before the filesystem move. An interrupted adoption can
    // then recover from either the temporary file or its recorded destination.
    this.database.write((tx) => {
      const row = this.checked_row(tx, checked);
      if (!['admitted', 'linked'].includes(row.state))
        throw new VideoUploadError('Video admission is no longer active.', 409);
      if (row.conversation_id !== null && row.conversation_id !== conversation_id) {
        throw new VideoUploadError('This video belongs to a different conversation.', 409);
      }
      tx.run('UPDATE conversation_video_uploads SET conversation_id=? WHERE upload_id=?', [
        conversation_id,
        checked.upload_id,
      ]);
    });
    return this.database.write((tx) => {
      const row = this.checked_row(tx, checked);
      const target = this.path(row);
      mkdirSync(dirname(target), { recursive: true });
      const source = this.temporary(checked.upload_id);
      if (file_stat(source) !== null) renameSync(source, target);
      const actual = file_stat(target);
      if (!actual?.isFile() || actual.size !== row.byte_count) {
        throw new VideoUploadError('The video file is missing. Retry the attachment.', 409);
      }
      const metadata = target + MEDIA_TYPE_SUFFIX;
      const temporary = join(dirname(target), '.' + randomUUID().replaceAll('-', ''));
      try {
        // A ready upload carries the media type from the completed probe.
        if (row.media_type === null) throw new TypeError('data must be str, not NoneType');
        writeFileSync(temporary, row.media_type, 'utf8');
        renameSync(temporary, metadata);
      } finally {
        remove_if_present(temporary);
      }
      return {
        conversation_id,
        stored_file_id: row.stored_file_id,
        media_type: row.media_type,
        absolute_path: target,
        byte_count: row.byte_count,
      };
    });
  }

  finish(checked: CheckedVideoUpload, outcome: VideoUploadOutcome): void {
    this.database.write((tx) => {
      const row = this.checked_row(tx, checked);
      // Acceptance pins the file permanently, including across later refusals.
      if (row.state === 'linked') return;
      if (outcome === 'refused' && row.conversation_id !== null) {
        const source = this.path(row);
        const temporary = this.temporary(checked.upload_id);
        if (file_stat(source) !== null) renameSync(source, temporary);
        if (!file_stat(temporary)?.isFile())
          throw new VideoUploadError('The video file is missing. Attach it again.', 409);
        remove_if_present(source + MEDIA_TYPE_SUFFIX);
        tx.run('UPDATE conversation_video_uploads SET conversation_id=NULL WHERE upload_id=?', [
          checked.upload_id,
        ]);
      }
      const state: Record<VideoUploadOutcome, UploadState> = {
        accepted: 'linked',
        refused: 'ready',
        uncertain: 'admitted',
      };
      tx.run('UPDATE conversation_video_uploads SET state=?,outcome=? WHERE upload_id=?', [
        state[outcome],
        outcome,
        checked.upload_id,
      ]);
    });
  }
}
