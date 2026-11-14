/** Actual unpack work runs only in the invocation-owned private child. */
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { constants } from 'node:fs';
import { chmod, lchmod, link, mkdir, open, symlink } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { Readable, Writable } from 'node:stream';
import { Transform } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { createGunzip } from 'node:zlib';
import { AppValidationError, MacArchiveInspectionError, MacArchiveJsonError } from './contracts.ts';
import type {
  ArchiveUnpackRequest,
  ArchiveUnpackResult,
  MacInspectionFailure,
} from './contracts.ts';
import { MAX_MAC_RUNTIME_ARCHIVE_BYTES } from '../updates/mac/configuration.ts';
import { validate_app_manifest } from './identity.ts';
import { ArchiveMembers } from './logic/archive_members.ts';
import { set_archive_times } from './archive_times.ts';
import {
  ARCHIVE_MAX_COMPRESSED_BYTES,
  ARCHIVE_MAX_EXPANDED_BYTES,
  ARCHIVE_STREAM_CHUNK_BYTES,
  ARCHIVE_SPOOL_BATCH_BYTES,
  ARCHIVE_MAX_MESSAGE_BYTES,
} from './configuration.ts';

export type UnpackRequest = ArchiveUnpackRequest;
export function mac_inspection_failure(error: unknown): MacInspectionFailure {
  if (error instanceof MacArchiveInspectionError)
    return { type: 'mac-inspection-error', kind: error.kind, message: error.message };
  if (error instanceof AppValidationError)
    return { type: 'mac-inspection-error', kind: 'archive', message: error.message };
  const actual = error instanceof MacArchiveJsonError ? error.original : error;
  const message = actual instanceof Error ? actual.message : String(actual);
  const name = actual instanceof Error ? actual.name : 'Error';
  if (error instanceof MacArchiveJsonError)
    return { type: 'mac-inspection-error', kind: 'json', name, message };
  if (
    actual instanceof Error &&
    'errno' in actual &&
    typeof actual.errno === 'number' &&
    'code' in actual &&
    typeof actual.code === 'string'
  ) {
    const fields: { syscall?: string; path?: string; dest?: string } = {};
    for (const key of ['syscall', 'path', 'dest'] as const) {
      const value: unknown = Reflect.get(actual, key);
      if (typeof value === 'string') fields[key] = value;
    }
    return {
      type: 'mac-inspection-error',
      kind: 'os',
      name,
      message,
      errno: actual.errno,
      code: actual.code,
      ...fields,
    };
  }
  return { type: 'mac-inspection-error', kind: 'unexpected', name, message };
}
export async function* read_archive_messages(
  stream: Readable,
): AsyncGenerator<Record<string, unknown>> {
  let pending = Buffer.alloc(0);
  for await (const piece of stream) {
    const chunk: Buffer = Buffer.isBuffer(piece) ? piece : Buffer.from(piece);
    let start = 0;
    while (start < chunk.length) {
      const newline = chunk.indexOf(10, start),
        end = newline < 0 ? chunk.length : newline;
      if (pending.length + end - start > ARCHIVE_MAX_MESSAGE_BYTES)
        throw new AppValidationError('app archive child message exceeds limit');
      pending = Buffer.concat([pending, chunk.subarray(start, end)]);
      if (newline < 0) break;
      const value: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(pending));
      if (!value || typeof value !== 'object' || Array.isArray(value))
        throw new AppValidationError('app archive child message is invalid');
      pending = Buffer.alloc(0);
      start = end + 1;
      yield value as Record<string, unknown>;
    }
  }
  if (pending.length) throw new AppValidationError('app archive child message is truncated');
}
export function write_archive_message(stream: Writable, value: unknown): Promise<void> {
  const bytes = JSON.stringify(value) + '\n';
  if (Buffer.byteLength(bytes) > ARCHIVE_MAX_MESSAGE_BYTES)
    return Promise.reject(new AppValidationError('app archive child message exceeds limit'));
  return new Promise((resolve, reject) =>
    stream.write(bytes, (error) => (error ? reject(error) : resolve())),
  );
}
async function write_all(file: FileHandle, bytes: Buffer, position: number): Promise<void> {
  let written = 0;
  while (written < bytes.length) {
    const result = await file.write(bytes, written, bytes.length - written, position + written);
    if (!result.bytesWritten) throw new AppValidationError('app archive write made no progress');
    written += result.bytesWritten;
  }
}
async function extract_members(
  spool: FileHandle,
  destination: string,
  reader: ArchiveMembers,
  fence: () => void,
  source_umask: number | null,
): Promise<void> {
  const buffer = Buffer.alloc(ARCHIVE_STREAM_CHUNK_BYTES);
  const directories = new Set<string>([destination]);
  const directory = async (path: string): Promise<void> => {
    fence();
    await mkdir(path, { recursive: true, mode: 0o700 });
    for (let current = path; current !== destination; current = dirname(current))
      directories.add(current);
  };
  for (const member of reader.members) {
    fence();
    const path = join(destination, member.path);
    if (member.type === '5') await directory(path);
    else if (!['1', '2'].includes(member.type)) {
      await directory(dirname(path));
      fence();
      const file = await open(path, 'wx', 0o600);
      try {
        let offset = 0;
        while (offset < member.size) {
          fence();
          const { bytesRead } = await spool.read(
            buffer,
            0,
            Math.min(buffer.length, member.size - offset),
            member.offset + offset,
          );
          if (!bytesRead) throw new AppValidationError('app archive spool is truncated');
          fence();
          await write_all(file, buffer.subarray(0, bytesRead), offset);
          offset += bytesRead;
        }
        await file.sync();
      } finally {
        await file.close();
      }
    }
  }
  for (const member of reader.members.filter((member) => ['1', '2'].includes(member.type))) {
    fence();
    const path = join(destination, member.path);
    await directory(dirname(path));
    fence();
    if (member.type === '1') await link(join(destination, member.linkpath), path);
    else {
      await symlink(member.linkpath, path);
      if (source_umask !== null && process.platform === 'darwin') {
        // Python source extraction keeps the caller's symlink creation mode on Darwin.
        fence();
        await lchmod(path, 0o777 & ~source_umask);
      }
    }
  }
  const ordinary = reader.members.filter((member) => member.path && member.type !== '2');
  for (const member of ordinary.filter((member) => member.type !== '5')) {
    fence();
    let mode = member.mode;
    if (source_umask !== null) {
      // Python tarfile's data_filter: owner RW, no go-w, owner-X gates all X.
      mode &= 0o755;
      if (!(mode & 0o100)) mode &= ~0o111;
      mode |= 0o600;
    }
    // A preceding hard-link member can already have removed read access on this inode.
    await chmod(join(destination, member.path), mode | 0o400);
    const file = await open(
      join(destination, member.path),
      constants.O_RDONLY | constants.O_NOFOLLOW,
    );
    try {
      set_archive_times(file.fd, member.mtime, fence);
      await file.chmod(mode);
      await file.sync();
    } finally {
      await file.close();
    }
  }
  const declared = new Map(
    reader.members
      .filter((member) => member.type === '5')
      .map((member) => [join(destination, member.path), member]),
  );
  for (const path of [...directories].sort((a, b) => b.split('/').length - a.split('/').length)) {
    fence();
    const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const member = declared.get(path);
      if (member) set_archive_times(file.fd, member.mtime, fence);
      await file.chmod(
        path === destination
          ? 0o700
          : source_umask === null
            ? (member?.mode ?? 0o700)
            : 0o777 & ~source_umask,
      );
      await file.sync();
    } finally {
      await file.close();
    }
  }
}
export async function unpack_archive_child(
  request: UnpackRequest,
  input: Readable,
  output: Writable,
  inherited_umask: number,
): Promise<void> {
  const controller = new AbortController();
  const lost = (): void =>
    controller.abort(new AppValidationError('app archive parent channel closed'));
  const fence = (): void => {
    if (controller.signal.aborted) throw controller.signal.reason;
  };
  input.once('end', lost);
  input.once('close', lost);
  input.once('error', lost);
  const replies = read_archive_messages(input);
  let watching: Promise<void> | undefined;
  let expecting_ack = false;
  const reserve_reply = () => {
    const pending = replies.next();
    watching = pending.then((response) => {
      if (response.done || !expecting_ack) lost();
    }, lost);
    return pending;
  };
  let pending_reply = reserve_reply();
  const acknowledge = async (message: unknown): Promise<void> => {
    fence();
    expecting_ack = true;
    await write_archive_message(output, message);
    const response = await pending_reply;
    expecting_ack = false;
    if (response.done || response.value.ok !== true)
      throw new AppValidationError('app archive parent acknowledgement is unavailable');
    pending_reply = reserve_reply();
    fence();
  };
  const inspection = request.purpose === 'mac-inspection';
  const source_export = request.purpose === 'source-export';
  let spool: FileHandle | undefined;
  try {
    if (!inspection) spool = await open(join(request.spool_root, 'expanded.tar'), 'wx+', 0o600);
    const compressed_limit = inspection
      ? MAX_MAC_RUNTIME_ARCHIVE_BYTES
      : ARCHIVE_MAX_COMPRESSED_BYTES;
    let checked_sha256: string | undefined;
    if (!source_export) {
      const receipt = createHash('sha256');
      let bytes = 0;
      for await (const chunk of createReadStream('', {
        fd: 3,
        start: 0,
        autoClose: false,
        highWaterMark: ARCHIVE_STREAM_CHUNK_BYTES,
        signal: controller.signal,
      })) {
        fence();
        bytes += chunk.length;
        if (bytes > compressed_limit)
          throw new AppValidationError('app archive exceeds compressed size limit');
        receipt.update(chunk);
      }
      fence();
      if (request.purpose === 'mac-inspection' && bytes !== request.archive_size)
        throw new MacArchiveInspectionError(
          'refusal',
          'The Mac runtime archive size differs from its publication.',
        );
      checked_sha256 = receipt.digest('hex');
      if (checked_sha256 !== request.archive_sha256) {
        if (request.purpose === 'mac-inspection')
          throw new MacArchiveInspectionError(
            'refusal',
            'The Mac runtime archive differs from its publication.',
          );
        throw new AppValidationError('app archive hash differs from publication receipt');
      }
    }
    const reader = new ArchiveMembers(inspection ? 'mac-inspection' : 'extraction');
    const second_digest = createHash('sha256');
    let compressed = 0,
      expanded = 0,
      credit = 0,
      magic = Buffer.alloc(0);
    const hashing = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        try {
          compressed += chunk.length;
          if (compressed > compressed_limit)
            throw new AppValidationError('app archive exceeds compressed size limit');
          if (magic.length < 2) {
            magic = Buffer.concat([magic, chunk.subarray(0, 2 - magic.length)]);
            if (!source_export && magic.length === 2 && (magic[0] !== 0x1f || magic[1] !== 0x8b))
              throw new AppValidationError('app archive is not gzip data');
          }
          second_digest.update(chunk);
          callback(null, chunk);
        } catch (error) {
          callback(error instanceof Error ? error : new Error(String(error)));
        }
      },
    });
    const decoding = source_export
      ? new Transform({
          transform(chunk, _encoding, callback) {
            callback(null, chunk);
          },
        })
      : createGunzip({ chunkSize: ARCHIVE_STREAM_CHUNK_BYTES });
    const input_file = createReadStream('', {
      fd: 3,
      start: 0,
      autoClose: false,
      highWaterMark: ARCHIVE_STREAM_CHUNK_BYTES,
    });
    let origin: { source: 'input' | 'decoding' | 'processing'; error: unknown } | undefined;
    input_file.once('error', (error) => {
      origin ??= { source: 'input', error };
    });
    decoding.once('error', (error) => {
      origin ??= { source: 'decoding', error };
    });
    try {
      await pipeline(
        input_file,
        hashing,
        decoding,
        async (source) => {
          for await (const value of source) {
            try {
              const bytes = Buffer.from(value);
              if (expanded + bytes.length > ARCHIVE_MAX_EXPANDED_BYTES)
                throw new AppValidationError('app archive exceeds expanded size limit');
              reader.feed(bytes);
              if (inspection) {
                expanded += bytes.length;
                continue;
              }
              let start = 0;
              while (start < bytes.length) {
                if (!credit) {
                  await acknowledge({ type: 'space', bytes: ARCHIVE_SPOOL_BATCH_BYTES });
                  credit = ARCHIVE_SPOOL_BATCH_BYTES;
                }
                const count = Math.min(credit, bytes.length - start);
                await write_all(spool!, bytes.subarray(start, start + count), expanded);
                expanded += count;
                credit -= count;
                start += count;
              }
            } catch (error) {
              origin ??= { source: 'processing', error };
              throw error;
            }
          }
        },
        { signal: controller.signal },
      );
    } catch (error) {
      // Retain the first origin before pipeline propagates the same error through its peers.
      const failure = inspection && !controller.signal.aborted && origin ? origin.error : error;
      if (
        inspection &&
        origin?.source === 'decoding' &&
        origin.error === failure &&
        !controller.signal.aborted
      )
        throw new MacArchiveInspectionError(
          'archive',
          failure instanceof Error ? failure.message : String(failure),
        );
      throw failure;
    }
    const parsed_sha256 = second_digest.digest('hex');
    if (checked_sha256 !== undefined && parsed_sha256 !== checked_sha256) {
      if (inspection)
        throw new MacArchiveInspectionError(
          'refusal',
          'The Mac runtime archive changed during inspection.',
        );
      throw new AppValidationError('app archive changed during extraction');
    }
    reader.finish();
    if (request.purpose === 'mac-inspection') {
      await write_archive_message(output, {
        type: 'mac-inspection-result',
        manifest_bytes: reader.manifest_bytes?.toString('base64') ?? null,
        python: reader.python,
        node: reader.node,
        entrypoint: reader.entrypoint,
      });
      fence();
      return;
    }
    await spool!.sync();
    await acknowledge({ type: 'validated', expanded_bytes: expanded });
    await extract_members(
      spool!,
      request.destination,
      reader,
      fence,
      source_export ? inherited_umask : null,
    );
    fence();
    let result: ArchiveUnpackResult;
    if (request.purpose === 'application') {
      const manifest = await validate_app_manifest(join(request.destination, 'manifest.json'), {
        expected_sha: request.expected_sha,
        require_runtime: true,
        verify_artifact_digest: false,
      });
      fence();
      result = { type: 'result', manifest };
    } else {
      result = { type: 'source-export-result', source_sha256: parsed_sha256 };
    }
    await write_archive_message(output, result);
    fence();
  } finally {
    if (watching) {
      input.destroy();
      await watching;
    }
    input.off('end', lost);
    input.off('close', lost);
    input.off('error', lost);
    await spool?.close();
  }
}
