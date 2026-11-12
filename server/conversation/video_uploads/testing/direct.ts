/** Export-only upload proof. Every disk budget and media answer is synthetic. */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createDatabase } from '../../../core/database/index.ts';
import { VideoUploadService } from '../service.ts';
import { VideoUploadError } from '../contracts.ts';
import type { ReserveVideoUpload, VideoUploadStatus } from '../contracts.ts';
import type { VideoUploadLimits } from '../configuration.ts';
interface Operation {
  op: 'reserve' | 'transfer' | 'cancel' | 'status' | 'free' | 'now' | 'cleanup' | 'admit';
  name: string;
  filename?: string;
  byte_count?: number;
  chunks?: readonly string[];
  interrupted?: boolean;
  value?: number;
}
interface Row extends Record<string, unknown> {
  upload_id: string;
  stored_file_id: string;
  conversation_id: string | null;
}
interface Spec {
  db_path: string;
  now: number;
  free: number;
  limits: VideoUploadLimits;
  operations: readonly Operation[];
}
export async function run(spec: Spec): Promise<unknown> {
  const database = createDatabase(spec.db_path);
  let now = spec.now;
  let free = spec.free;
  let requires = 0;
  const references = new Map<string, string>();
  const names = new Map<string, string>();
  const probe_calls: unknown[] = [];
  const service = new VideoUploadService(database, spec.db_path, {
    now: () => now,
    limits: spec.limits,
    disk_free_bytes: () => free,
    require_probe: () => {
      requires++;
    },
    probe_video: async (path, filename) => {
      probe_calls.push({
        upload: names.get(path.slice(path.lastIndexOf('/') + 1)),
        filename,
        bytes: readFileSync(path).toString('hex'),
      });
      if (filename === 'rejected.mp4') throw new VideoUploadError('Synthetic media rejection.');
      return 'video/mp4';
    },
  });
  const bytes = (path: string): string | null =>
    existsSync(path) ? readFileSync(path).toString('hex') : null;
  const snapshot = () => {
    const rows = database.read((sql) =>
      sql.all<Row>('SELECT * FROM conversation_video_uploads ORDER BY rowid'),
    );
    return {
      rows: rows.map((row) => ({
        ...row,
        upload_id: names.get(row.upload_id),
        stored_file_id: 'stored:' + names.get(row.upload_id),
      })),
      files: Object.fromEntries(
        rows.map((row) => {
          const name = names.get(row.upload_id)!;
          const target =
            row.conversation_id === null
              ? null
              : join(
                  dirname(spec.db_path),
                  'files',
                  'conversations',
                  row.conversation_id,
                  row.stored_file_id,
                );
          return [
            name,
            {
              temporary: bytes(join(service.root, row.upload_id)),
              adopted: target === null ? null : bytes(target),
              metadata: target === null ? null : bytes(target + '.media-type'),
            },
          ];
        }),
      ),
    };
  };
  const answers: unknown[] = [];
  try {
    for (const operation of spec.operations) {
      let answer: unknown = null;
      try {
        const id = references.get(operation.name)!;
        if (operation.op === 'reserve') {
          const request: ReserveVideoUpload = {
            destination: { kind: 'chief', id: 'chief', expected_conversation_id: null },
            filename: operation.filename!,
            byte_count: operation.byte_count!,
          };
          const result = service.reserve(request);
          references.set(operation.name, result.upload_id);
          names.set(result.upload_id, operation.name);
          answer = { ...result, upload_id: operation.name };
        } else if (operation.op === 'transfer') {
          const chunks = async function* () {
            for (const chunk of operation.chunks ?? []) yield Buffer.from(chunk, 'hex');
            if (operation.interrupted) throw new VideoUploadError('Synthetic interrupted stream.');
          };
          const result = await service.transfer(id, chunks());
          answer = { ...result, upload_id: operation.name };
        } else if (operation.op === 'cancel') service.cancel(id);
        else if (operation.op === 'status')
          answer = { ...service.status(id), upload_id: operation.name } satisfies VideoUploadStatus;
        else if (operation.op === 'free') free = operation.value!;
        else if (operation.op === 'now') now = operation.value!;
        else if (operation.op === 'cleanup') service.recover_and_cleanup();
        else if (operation.op === 'admit') {
          const result = service.preflight(
            id,
            { kind: 'chief', id: 'chief', expected_conversation_id: null },
            'sender:' + operation.name,
          );
          answer = { ...result, upload_id: operation.name };
        }
        answers.push({
          name: operation.name,
          op: operation.op,
          status: 200,
          answer,
          ...snapshot(),
        });
      } catch (error) {
        if (!(error instanceof VideoUploadError)) throw error;
        answers.push({
          name: operation.name,
          op: operation.op,
          status: error.status_code,
          answer: { detail: error.message },
          ...snapshot(),
        });
      }
    }
    return { answers, probe_calls, requires };
  } finally {
    database.close();
  }
}
