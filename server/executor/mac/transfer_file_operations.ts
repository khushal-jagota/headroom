/** Bounded transfer bytes, hashes and full flush stay in the existing owned file worker. */
import { constants, fstatSync, readSync, writeSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { basename, dirname } from 'node:path';
import { isMainThread } from 'node:worker_threads';
import {
  close_native_descriptor,
  open_directory_descriptor,
  open_relative,
  sync_file_descriptor,
  unlink_relative,
} from './platform.ts';
import { TRANSFER_CHUNK_BYTES } from './configuration.ts';
import { file_error } from './file_metadata.ts';
import { close_retained } from './retained_cleanup.ts';
if (isMainThread) throw new Error('Executor transfers require the owned FilesService worker.');
interface Transfer {
  descriptor: number | null;
  parent: number | null;
  readonly name: string;
  created: boolean;
  discard: boolean;
  readonly mode: 'read' | 'write';
  readonly digest: ReturnType<typeof createHash>;
  count: bigint;
  finished: boolean;
}
const transfers = new Map<number, Transfer>();
let sequence = 0;
function transfer(handle: number, mode?: Transfer['mode']): Transfer {
  const value = transfers.get(handle);
  if (!value || (mode !== undefined && (value.mode !== mode || value.finished)))
    throw new Error('Executor transfer is closed or has the wrong mode.');
  return value;
}
function descriptor(value: Transfer): number {
  if (value.descriptor === null) throw new Error('Executor transfer descriptor is closed.');
  return value.descriptor;
}
export function open_transfer(path: string, mode: Transfer['mode']): number {
  const value: Transfer = {
    descriptor: null,
    parent: null,
    name: basename(path),
    created: false,
    discard: false,
    mode,
    digest: createHash('sha256'),
    count: 0n,
    finished: false,
  };
  const handle = ++sequence;
  transfers.set(handle, value);
  try {
    value.parent = open_directory_descriptor(dirname(path));
    value.descriptor = open_relative(
      value.parent,
      basename(path),
      mode === 'write'
        ? constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW
        : constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      0o600,
    );
    value.created = mode === 'write';
    if (!fstatSync(value.descriptor).isFile()) throw file_error('artifact is not regular file');
    return handle;
  } catch (error) {
    try {
      close_transfer(handle);
    } catch (cleanup) {
      throw new AggregateError([error, cleanup], 'Transfer admission and native cleanup failed.');
    }
    throw error;
  }
}
export function read_transfer(handle: number, limit: number): Uint8Array {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > TRANSFER_CHUNK_BYTES)
    throw file_error('invalid transfer read limit');
  const value = transfer(handle, 'read'),
    buffer = Buffer.allocUnsafe(limit);
  const count = readSync(descriptor(value), buffer, 0, buffer.length, null);
  const bytes = buffer.subarray(0, count);
  value.digest.update(bytes);
  value.count += BigInt(count);
  return bytes;
}
export function write_transfer(handle: number, bytes: Uint8Array): void {
  const value = transfer(handle, 'write');
  if (bytes.byteLength > TRANSFER_CHUNK_BYTES)
    throw new Error('Executor transfer chunk exceeds its bound.');
  let offset = 0;
  while (offset < bytes.length) {
    const count = writeSync(descriptor(value), bytes, offset, bytes.length - offset, null);
    if (count <= 0) throw new Error('short file write');
    offset += count;
  }
  value.digest.update(bytes);
  value.count += BigInt(bytes.length);
}
export function finish_transfer(handle: number, sha256: string, byte_count: bigint): void {
  const value = transfer(handle);
  if (value.finished) throw new Error('Executor transfer already finished.');
  value.finished = true;
  if (value.count !== byte_count || value.digest.digest('hex') !== sha256)
    throw file_error('incomplete or corrupt file');
  if (value.mode === 'write') sync_file_descriptor(descriptor(value));
}
function close(value: Transfer): void {
  try {
    if (value.discard && value.parent !== null) {
      unlink_relative(value.parent, value.name, true);
      value.discard = false;
      value.created = false;
    }
    if (value.descriptor !== null) {
      close_native_descriptor(value.descriptor);
      value.descriptor = null;
    }
    if (value.parent !== null) {
      close_native_descriptor(value.parent);
      value.parent = null;
    }
  } catch (error) {
    // A failed download close remains a failed download. Its retained owner
    // retries removal and native close together, without a path-based unlink.
    if (value.created) value.discard = true;
    throw error;
  }
}
export function close_transfer(handle: number, discard = false): void {
  const value = transfer(handle);
  if (discard) {
    if (value.mode !== 'write') throw new Error('Cannot discard an upload source.');
    value.discard = true;
  }
  close(value);
  transfers.delete(handle);
}
export function close_transfers(): unknown {
  return close_retained(transfers, close);
}
