import { constants } from 'node:fs';
import { lstat, open, rename, unlink } from 'node:fs/promises';
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { dirname, isAbsolute, join, relative } from 'node:path';
import { managed_file_roots } from '../environments/backup/capture.ts';
import { resolved_path } from '../environments/paths.ts';
import { CAPABILITY_FILENAME, MAX_CONTROL_MESSAGE_BYTES } from './configuration.ts';
import { ManagedStopError } from './contracts.ts';
import type { CapabilityCaptureRoots, StopCapabilityRecord, SupervisorMode } from './contracts.ts';
import { decode_capability, encode_message } from './logic/control.ts';

function contains(parent: string, child: string): boolean {
  const tail = relative(parent, child);
  return tail !== '..' && !tail.startsWith('../') && !isAbsolute(tail);
}
export async function admit_capability_placement(
  runtime_directory: string,
  roots: CapabilityCaptureRoots,
): Promise<void> {
  if (!isAbsolute(runtime_directory))
    throw new ManagedStopError('Managed runtime directory must be absolute');
  const info = await lstat(runtime_directory);
  if (
    !info.isDirectory() ||
    info.isSymbolicLink() ||
    info.uid !== process.getuid!() ||
    (info.mode & 0o777) !== 0o700
  )
    throw new ManagedStopError('Managed runtime directory must be an owned private0700directory');
  const runtime = await resolved_path(runtime_directory);
  for (const root of [
    roots.application_root,
    roots.database_parent,
    ...Object.values(roots.managed_roots),
    roots.snapshot_destination,
  ]) {
    const actual = await resolved_path(root);
    if (contains(actual, runtime) || contains(runtime, actual))
      throw new ManagedStopError(
        'Managed stop capability must be outside application and snapshot roots',
      );
  }
}
export async function capability_capture_roots(
  mode: Extract<SupervisorMode, { kind: 'managed-linux' }>,
): Promise<CapabilityCaptureRoots> {
  return {
    application_root: await resolved_path(mode.application_root),
    database_parent: dirname(await resolved_path(mode.database_path)),
    managed_roots: await managed_file_roots(mode.database_path),
    snapshot_destination: await resolved_path(mode.backup_root),
  };
}
export function capability_matches(
  record: StopCapabilityRecord,
  incarnation: string,
  token: string,
): boolean {
  const actual = Buffer.from(record.capability),
    candidate = Buffer.from(token);
  return (
    incarnation === record.incarnation &&
    actual.length === candidate.length &&
    timingSafeEqual(actual, candidate)
  );
}
export async function read_stop_capability(
  runtime_directory: string,
): Promise<StopCapabilityRecord> {
  const directory = await lstat(runtime_directory);
  if (
    !directory.isDirectory() ||
    directory.isSymbolicLink() ||
    directory.uid !== process.getuid!() ||
    (directory.mode & 0o777) !== 0o700
  )
    throw new ManagedStopError('Managed runtime directory is not private');
  const file = await open(
    join(runtime_directory, CAPABILITY_FILENAME),
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  try {
    const info = await file.stat();
    if (
      !info.isFile() ||
      info.uid !== process.getuid!() ||
      (info.mode & 0o777) !== 0o600 ||
      info.size > MAX_CONTROL_MESSAGE_BYTES
    )
      throw new ManagedStopError('Managed stop capability file is not private');
    return decode_capability(await file.readFile());
  } finally {
    await file.close();
  }
}
export async function publish_stop_capability(
  runtime_directory: string,
): Promise<{ record: StopCapabilityRecord; close(): Promise<void> }> {
  const path = join(runtime_directory, CAPABILITY_FILENAME);
  try {
    const existing = await lstat(path);
    if (
      !existing.isFile() ||
      existing.isSymbolicLink() ||
      existing.uid !== process.getuid!() ||
      (existing.mode & 0o777) !== 0o600
    )
      throw new ManagedStopError('Existing stop capability is not an owned private file');
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  const record: StopCapabilityRecord = {
    version: 2,
    incarnation: randomUUID(),
    capability: randomBytes(32).toString('hex'),
    supervisor_pid: process.pid,
  };
  const temporary = join(runtime_directory, `.stop-capability-${randomUUID()}`);
  const file = await open(temporary, 'wx', 0o600);
  try {
    await file.writeFile(encode_message(record));
    await file.close();
    await rename(temporary, path);
  } catch (error) {
    await file.close().catch(() => {});
    await unlink(temporary).catch(() => {});
    throw error;
  }
  const owned = await lstat(path);
  return {
    record,
    async close() {
      try {
        const actual = await lstat(path);
        if (
          actual.dev !== owned.dev ||
          actual.ino !== owned.ino ||
          (await read_stop_capability(runtime_directory)).incarnation !== record.incarnation
        )
          return;
        await unlink(path);
      } catch (error) {
        if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
      }
    },
  };
}
