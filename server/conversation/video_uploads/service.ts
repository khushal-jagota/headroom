/** Durable bounded intake, atomic adoption, and conservative recovery. */
import { randomUUID } from 'node:crypto';
import { mkdirSync, renameSync, statSync, statfsSync, unlinkSync, writeFileSync } from 'node:fs';
import { open } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { Database, ReadSql } from '../../core/database/contracts.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
import { observe_shutdown } from '../shutdown.ts';
import type { KeptMessageFile } from '../message_files.ts';
import { MEDIA_TYPE_SUFFIX } from '../record/message_files.ts';
import { destination_from_json, destination_to_json, VideoUploadError } from './contracts.ts';
import type {
  CheckedVideoUpload,
  ReserveVideoUpload,
  UploadDestination,
  UploadState,
  VideoUploadOutcome,
  VideoUploadStatus,
} from './contracts.ts';
import {
  expiry_eligible,
  require_capacity,
  validate_binding,
  validate_filename,
} from './logic/rules.ts';
import { VIDEO_UPLOAD_LIMITS } from './configuration.ts';
import type { VideoUploadLimits } from './configuration.ts';
import { probe_video, require_probe } from './probe.ts';

export interface VideoUploadServiceOptions {
  readonly now?: () => number;
  readonly limits?: Partial<VideoUploadLimits>;
  readonly probe_video?: (
    path: string,
    filename: string,
    options?: { signal?: AbortSignal },
  ) => Promise<string>;
  readonly signal?: AbortSignal;
  readonly require_probe?: () => unknown;
  readonly disk_free_bytes?: () => number;
}

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
  private sealed = false;
  private readonly transfers = new Set<Promise<VideoUploadStatus>>();
  readonly database: Database;
  readonly db_path: string;
  readonly root: string;
  private readonly now: () => number;
  private readonly limits: Readonly<VideoUploadLimits>;
  private readonly probe: (
    path: string,
    filename: string,
    options?: { signal?: AbortSignal },
  ) => Promise<string>;
  private readonly signal: AbortSignal | undefined;
  private readonly require_probe: () => unknown;
  private readonly disk_free_bytes: () => number;

  constructor(database: Database, db_path: string, options: VideoUploadServiceOptions = {}) {
    this.database = database;
    this.db_path = db_path;
    this.root = join(dirname(db_path), 'files', 'video-uploads');
    mkdirSync(this.root, { recursive: true });
    this.now = options.now ?? (() => Math.floor(Date.now() / 1000));
    this.limits = { ...VIDEO_UPLOAD_LIMITS, ...options.limits };
    this.probe = options.probe_video ?? probe_video;
    this.signal = options.signal;
    this.require_probe = options.require_probe ?? require_probe;
    this.disk_free_bytes =
      options.disk_free_bytes ??
      (() => {
        const usage = statfsSync(this.root);
        return usage.bavail * usage.bsize;
      });
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

  private disk_capacity(sql: ReadSql, free_bytes: number, incoming = 0): void {
    const outstanding = sql.get<{ outstanding: number }>(`
      SELECT COALESCE(SUM(byte_count-received_bytes),0) AS outstanding
      FROM conversation_video_uploads
      WHERE state IN ('reserved','transferring')
    `)!.outstanding;
    if (free_bytes - outstanding - incoming < this.limits.free_space_floor_bytes)
      throw new VideoUploadError('Not enough free disk space for this video.', 507);
  }

  reserve(request: ReserveVideoUpload): VideoUploadStatus {
    if (this.sealed) throw new VideoUploadError('Video uploads are shutting down.', 503);
    this.require_probe();
    validate_filename(request.filename);
    if (request.byte_count > this.limits.max_video_bytes)
      throw new VideoUploadError('Video exceeds the 250 MiB limit.', 413);
    if (!request.destination.id || UNSAFE_IDENTIFIER_CHARACTER.test(request.destination.id))
      throw new VideoUploadError('Invalid destination.');
    const upload_id = 'vu_' + randomUUID().replaceAll('-', '');
    const free_bytes = this.disk_free_bytes();
    this.database.write((tx) => {
      const used = tx.get<{ used: number }>(`
        SELECT COALESCE(SUM(byte_count),0) AS used
        FROM conversation_video_uploads
        WHERE state IN ('reserved','transferring','ready','admitted','linked')
      `)!.used;
      require_capacity(used, request.byte_count, this.limits.video_budget_bytes);
      this.disk_capacity(tx, free_bytes, request.byte_count);
      tx.run(
        `
        INSERT INTO conversation_video_uploads
          (upload_id,destination_json,filename,byte_count,state,expires_at,stored_file_id)
        VALUES (?,?,?,?,?,?,?)
      `,
        [
          upload_id,
          destination_to_json(request.destination),
          request.filename,
          request.byte_count,
          'reserved',
          this.now() + this.limits.upload_ttl_seconds,
          'f_' + randomUUID().replaceAll('-', ''),
        ],
      );
    });
    return this.status(upload_id);
  }

  private remove(row: UploadRow): void {
    const path = this.path(row);
    remove_if_present(path);
    if (row.conversation_id !== null) remove_if_present(path + MEDIA_TYPE_SUFFIX);
    remove_if_present(this.temporary(row.upload_id));
  }

  cancel(upload_id: string): void {
    const row = this.database.read((sql) => this.row(sql, upload_id));
    if (['admitted', 'linked'].includes(row.state))
      throw new VideoUploadError('This video is in use. It cannot be removed.', 409);
    this.remove(row);
    this.database.write((tx) => {
      tx.run("UPDATE conversation_video_uploads SET state='cancelled' WHERE upload_id=?", [
        upload_id,
      ]);
    });
  }

  async transfer(upload_id: string, chunks: AsyncIterable<Uint8Array>): Promise<VideoUploadStatus> {
    if (this.sealed) throw new VideoUploadError('Video uploads are shutting down.', 503);
    const operation = this.receive_transfer(upload_id, chunks);
    this.transfers.add(operation);
    void operation.then(
      () => this.transfers.delete(operation),
      () => this.transfers.delete(operation),
    );
    return await operation;
  }

  seal(): void {
    this.sealed = true;
  }

  drain(options: StopOptions): Promise<void> {
    this.seal();
    return observe_shutdown(
      this.settled(),
      options,
      'Video transfers remain unfinished at the shutdown deadline.',
    );
  }

  async settled(): Promise<void> {
    while (this.transfers.size !== 0) await Promise.allSettled([...this.transfers]);
  }

  private async receive_transfer(
    upload_id: string,
    chunks: AsyncIterable<Uint8Array>,
  ): Promise<VideoUploadStatus> {
    this.signal?.throwIfAborted();
    const [declared, filename] = this.begin_transfer(upload_id);
    let received = 0;
    let recorded = 0;
    let output: FileHandle | undefined;
    try {
      output = await open(this.temporary(upload_id), 'wx');
      try {
        for await (const incoming of chunks) {
          this.signal?.throwIfAborted();
          for (
            let offset = 0;
            offset < incoming.length;
            offset += this.limits.transfer_chunk_bytes
          ) {
            const chunk = incoming.subarray(offset, offset + this.limits.transfer_chunk_bytes);
            if (received + chunk.length > declared)
              throw new VideoUploadError('Transfer exceeds its reserved size.', 413);
            received = await this.store_chunk(output, chunk, received);
            if (received - recorded >= this.limits.progress_record_bytes) {
              this.record_progress(upload_id, received);
              recorded = received;
            }
          }
        }
        this.record_progress(upload_id, received);
        await output.sync();
        this.signal?.throwIfAborted();
      } finally {
        await output.close();
        output = undefined;
      }
      if (received !== declared)
        throw new VideoUploadError('Video transfer was interrupted. Retry the attachment.');
      const media_type = await this.probe(this.temporary(upload_id), filename, {
        signal: this.signal,
      });
      this.signal?.throwIfAborted();
      return this.mark_ready(upload_id, media_type);
    } catch (error) {
      // File writes and failures occur outside the write door. Commit only facts.
      const disconnected =
        error instanceof Error &&
        (error.message === 'aborted' ||
          ('code' in error &&
            ['ECONNRESET', 'ERR_STREAM_PREMATURE_CLOSE', 'ABORT_ERR'].includes(
              String(error.code),
            )));
      const interrupted = this.signal?.aborted || disconnected;
      this.mark_failed(upload_id, interrupted ? new Error('Upload interrupted.') : error);
      this.signal?.throwIfAborted();
      if (disconnected) throw new Error('Upload interrupted.');
      if (error instanceof Error && 'code' in error)
        throw new VideoUploadError('Video could not be stored. Retry the attachment.', 507);
      throw error;
    }
  }

  private begin_transfer(upload_id: string): readonly [number, string] {
    return this.database.write((tx) => {
      const row = this.row(tx, upload_id);
      if (row.state !== 'reserved' || row.expires_at <= this.now())
        throw new VideoUploadError(
          'This upload is no longer available. Attach the video again.',
          409,
        );
      tx.run("UPDATE conversation_video_uploads SET state='transferring' WHERE upload_id=?", [
        upload_id,
      ]);
      return [row.byte_count, row.filename];
    });
  }

  private async store_chunk(
    output: FileHandle,
    chunk: Uint8Array,
    received: number,
  ): Promise<number> {
    this.signal?.throwIfAborted();
    const free_bytes = this.disk_free_bytes();
    if (free_bytes - chunk.length < this.limits.free_space_floor_bytes)
      throw new VideoUploadError('Video upload stopped: disk space is low.', 507);
    let written = 0;
    while (written < chunk.length) {
      const result = await output.write(chunk, written, chunk.length - written);
      if (result.bytesWritten === 0) throw new Error('Video file write made no progress.');
      written += result.bytesWritten;
      this.signal?.throwIfAborted();
    }
    return received + chunk.length;
  }

  private record_progress(upload_id: string, received: number): void {
    this.signal?.throwIfAborted();
    this.database.write((tx) => {
      if (this.row(tx, upload_id).state !== 'transferring')
        throw new VideoUploadError('The video upload was cancelled or expired.', 409);
      tx.run('UPDATE conversation_video_uploads SET received_bytes=? WHERE upload_id=?', [
        received,
        upload_id,
      ]);
      this.disk_capacity(tx, this.disk_free_bytes());
    });
  }

  private mark_ready(upload_id: string, media_type: string): VideoUploadStatus {
    this.database.write((tx) => {
      if (this.row(tx, upload_id).state !== 'transferring')
        throw new VideoUploadError('The video upload was cancelled or expired.', 409);
      tx.run("UPDATE conversation_video_uploads SET state='ready',media_type=? WHERE upload_id=?", [
        media_type,
        upload_id,
      ]);
    });
    return this.status(upload_id);
  }

  private mark_failed(upload_id: string, error: unknown): void {
    remove_if_present(this.temporary(upload_id));
    const message = error instanceof Error ? error.message : String(error ?? '');
    this.database.write((tx) => {
      tx.run("UPDATE conversation_video_uploads SET state='failed',error=? WHERE upload_id=?", [
        message || 'Upload interrupted.',
        upload_id,
      ]);
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
      if (row.state === 'ready' && row.expires_at <= this.now()) {
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

  recover_and_cleanup(): void {
    const now = this.now();
    const rows = this.database.read((sql) =>
      sql.all<UploadRow>('SELECT * FROM conversation_video_uploads'),
    );
    for (const row of rows) {
      if (row.state === 'admitted' && row.conversation_id) {
        this.database.write((tx) => {
          const accepted = tx.get(
            `
            SELECT 1 FROM conversation_events
            WHERE conversation_id=? AND kind='prompt'
              AND json_extract(payload,'$.sender_message_id')=?
            LIMIT 1
          `,
            [row.conversation_id, row.sender_message_id],
          );
          if (accepted)
            tx.run(
              "UPDATE conversation_video_uploads SET state='linked',outcome='accepted' WHERE upload_id=?",
              [row.upload_id],
            );
        });
      }
      if (expiry_eligible(row.state, row.expires_at, now)) {
        this.remove(row);
        this.database.write((tx) => {
          tx.run("UPDATE conversation_video_uploads SET state='cancelled' WHERE upload_id=?", [
            row.upload_id,
          ]);
        });
      }
    }
  }
}
