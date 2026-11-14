/** Receipt-bound input stays open through the actual owned unpack lifetime. */
import { createHash } from 'node:crypto';
import { constants, createReadStream, createWriteStream } from 'node:fs';
import {
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readdir,
  readlink,
  rm,
  stat,
  statfs,
} from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import type { ChildProcess } from 'node:child_process';
import { basename, dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { pipeline } from 'node:stream/promises';
import { Pack } from 'tar';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import { stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import { path_is_within } from '../../core/path_containment.ts';
import { is_record } from '../../core/records.ts';
import type {
  AppManifest,
  ArchiveUnpackRequest,
  ReleaseExecution,
  MacArchiveInspection,
  MacInspectionExecution,
} from './contracts.ts';
import { assert_release_execution } from './process.ts';
import {
  AppValidationError,
  MacArchiveInspectionError,
  MacArchiveInspectionUnsettled,
  MacArchiveJsonError,
} from './contracts.ts';
import { decode_mac_manifest } from '../updates/mac/manifest.ts';
import { MAX_MAC_RUNTIME_MANIFEST_BYTES } from '../updates/mac/configuration.ts';
import {
  ARCHIVE_GZIP_LEVEL,
  FILE_CHUNK_BYTES,
  ARCHIVE_MAX_COMPRESSED_BYTES,
  ARCHIVE_MAX_EXPANDED_BYTES,
  ARCHIVE_SPOOL_BATCH_BYTES,
  ARCHIVE_SPACE_RESERVE_BYTES,
} from './configuration.ts';
import { app_tree_paths, validate_app_manifest } from './identity.ts';
import { read_archive_messages, write_archive_message } from './archive_unpack.ts';
import { resolved_path } from '../paths.ts';
export { APP_ARCHIVE_NAME, PUBLICATION_RECEIPT_NAME } from './configuration.ts';

export async function hash_archive(path: string): Promise<string> {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path, { highWaterMark: FILE_CHUNK_BYTES }))
    digest.update(chunk);
  return digest.digest('hex');
}
export async function create_app_archive(app: string, archive: string): Promise<string> {
  await validate_app_manifest(join(app, 'manifest.json'), { require_runtime: true });
  const root = await resolved_path(app),
    target = await resolved_path(archive);
  if (path_is_within(root, target))
    throw new AppValidationError('archive must be outside the candidate app');
  const paths = await app_tree_paths(root);
  for (const path of paths) {
    const info = await lstat(path);
    if (info.isSymbolicLink() && isAbsolute(await readlink(path)))
      throw new AppValidationError('app archive requires relative symlinks');
    if (info.mode & 0o7777 & ~0o777)
      throw new AppValidationError('app archive contains special permission bits');
  }
  const pack = new Pack({
    cwd: root,
    gzip: { level: ARCHIVE_GZIP_LEVEL },
    strict: true,
    noDirRecurse: true,
  });
  const writing = pipeline(pack, createWriteStream(archive, { flags: 'wx' }));
  // Pack.add treats a filename beginning with '@' as an ordinary path.
  for (const path of paths) pack.add(relative(root, path));
  pack.end();
  await writing;
  return hash_archive(archive);
}
async function filesystem_free_bytes(path: string): Promise<bigint> {
  const info = await statfs(path, { bigint: true });
  return info.bavail * info.bsize;
}
async function existing_parent(path: string): Promise<string> {
  let current = path;
  while (true) {
    try {
      if (!(await stat(current)).isDirectory())
        throw new AppValidationError('archive destination parent is not a directory');
      return current;
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
      const next = dirname(current);
      if (next === current) throw error;
      current = next;
    }
  }
}
export async function remove_owned_tree(path: string): Promise<void> {
  // Archived modes can remove owner access. Restore it without following links.
  async function accessible(directory: string): Promise<void> {
    await chmod(directory, 0o700);
    for (const entry of await readdir(directory, { withFileTypes: true }))
      if (entry.isDirectory()) await accessible(join(directory, entry.name));
  }
  await accessible(path);
  await rm(path, { recursive: true, force: true });
}
export async function extract_app_archive(
  archive: string,
  destination: string,
  archive_sha256: string,
  expected_sha: string,
  child_processes: OwnedChildProcesses,
  read_free_space: (path: string) => Promise<bigint> = filesystem_free_bytes,
  execution?: ReleaseExecution,
): Promise<AppManifest> {
  return (await extract_archive(
    archive,
    destination,
    { purpose: 'application', archive_sha256, expected_sha },
    child_processes,
    read_free_space,
    execution,
  )) as AppManifest;
}
export async function extract_git_export(
  archive: string,
  destination: string,
  execution: ReleaseExecution,
): Promise<void> {
  await extract_archive(
    archive,
    destination,
    { purpose: 'source-export' },
    execution.child_processes,
    filesystem_free_bytes,
    execution,
  );
}
export async function inspect_mac_archive(
  archive: string,
  archive_sha256: string,
  archive_size: number,
  execution: MacInspectionExecution,
): Promise<MacArchiveInspection> {
  if (!Number.isFinite(execution.deadline))
    throw new AppValidationError('Mac archive inspection requires a finite deadline');
  execution.signal.throwIfAborted();
  if (performance.now() / 1000 >= execution.deadline)
    throw new AppValidationError('Mac archive inspection deadline expired before admission');
  const signal = AbortSignal.any([
    execution.signal,
    AbortSignal.timeout(Math.max(0, Math.ceil((execution.deadline - performance.now() / 1000) * 1000))),
  ]);
  let interrupt: ((reason: unknown) => void) | undefined;
  let stopped!: (reason: unknown) => void;
  const interrupted = new Promise<never>((_resolve, reject) => {
    stopped = reject;
  });
  const abort = () => {
    interrupt?.(signal.reason);
    stopped(signal.reason);
  };
  signal.addEventListener('abort', abort);
  const actual = extract_archive(
    archive,
    null,
    { purpose: 'mac-inspection', archive_sha256, archive_size },
    execution.child_processes,
    filesystem_free_bytes,
    undefined,
    (cancel) => {
      interrupt = cancel;
    },
  );
  let complete = false;
  void actual.then(
    () => {
      complete = true;
    },
    () => {
      complete = true;
    },
  );
  const settled = actual.then(() => {});
  void settled.catch(() => {});
  try {
    return (await Promise.race([actual, interrupted])) as MacArchiveInspection;
  } catch (error) {
    interrupt?.(error);
    if (!complete) throw new MacArchiveInspectionUnsettled(settled, error);
    throw error;
  } finally {
    signal.removeEventListener('abort', abort);
  }
}
function inspection_error(message: Record<string, unknown>): Error {
  if (typeof message.message !== 'string') throw new Error('invalid Mac inspection error');
  if (message.kind === 'archive' || message.kind === 'refusal')
    return new MacArchiveInspectionError(message.kind, message.message);
  if (
    !['os', 'json', 'unexpected'].includes(String(message.kind)) ||
    typeof message.name !== 'string'
  )
    throw new Error('invalid Mac inspection error');
  const error = Object.assign(new Error(message.message), { name: message.name });
  if (message.kind === 'os') {
    if (typeof message.code !== 'string' || !Number.isSafeInteger(message.errno))
      throw new Error('invalid Mac inspection errno');
    Object.assign(error, { code: message.code, errno: message.errno });
    for (const key of ['syscall', 'path', 'dest']) {
      if (key in message && typeof message[key] !== 'string')
        throw new Error('invalid Mac inspection filesystem field');
      if (key in message) Object.assign(error, { [key]: message[key] });
    }
  }
  return error;
}
async function extract_archive(
  archive: string,
  destination: string | null,
  purpose:
    | Omit<Extract<ArchiveUnpackRequest, { purpose: 'application' }>, 'spool_root' | 'destination'>
    | { purpose: 'source-export' }
    | { purpose: 'mac-inspection'; archive_sha256: string; archive_size: number },
  child_processes: OwnedChildProcesses,
  read_free_space: (path: string) => Promise<bigint>,
  execution?: ReleaseExecution,
  register_cancel?: (cancel: (reason: unknown) => void) => void,
): Promise<AppManifest | string | MacArchiveInspection> {
  let input: FileHandle | undefined, work: string | undefined, child: ChildProcess | undefined;
  let closed: Promise<void> | undefined,
    ended = false,
    stop_requested = false,
    code: number | null = null;
  let ownOutput = false,
    success = false,
    result: AppManifest | string | MacArchiveInspection | undefined,
    failure: unknown;
  const errors: unknown[] = [];
  const inspection = purpose.purpose === 'mac-inspection';
  const signal = execution
    ? execution.deadline === null
      ? execution.signal
      : AbortSignal.any([
          execution.signal,
          AbortSignal.timeout(
            Math.max(0, Math.ceil((execution.deadline - performance.now() / 1000) * 1000)),
          ),
        ])
    : undefined;
  const cancel = (reason: unknown): void => {
    failure ??= reason;
    if (child && !ended && !stop_requested) {
      try {
        stop_one_shot_child(child);
        stop_requested = true;
      } catch (error) {
        errors.push(error);
      }
      child.stdin?.destroy();
    }
    if (inspection)
      child?.stdout?.destroy(reason instanceof Error ? reason : new Error(String(reason)));
  };
  register_cancel?.(cancel);
  const abort = () => cancel(signal!.reason);
  let target = destination === null ? '' : resolve(destination);
  const require_live = (): void => {
    if (failure !== undefined) throw failure;
    if (execution) assert_release_execution(execution);
    if (
      ended ||
      (child?.exitCode !== null && child?.exitCode !== undefined) ||
      (child?.signalCode !== null && child?.signalCode !== undefined)
    )
      throw new AppValidationError('app archive child stopped before completion');
  };
  const capacity = async (path: string, bytes: bigint, message: string): Promise<void> => {
    const free = await read_free_space(path);
    require_live();
    if (typeof free !== 'bigint' || free < bytes) throw new AppValidationError(message);
  };
  async function unpack(): Promise<AppManifest | string | MacArchiveInspection> {
    try {
      require_live();
      input = await open(archive, constants.O_RDONLY | constants.O_NONBLOCK);
      require_live();
      const info = await input.stat();
      require_live();
      if (!info.isFile()) throw new AppValidationError('app archive must be a regular file');
      if (purpose.purpose === 'mac-inspection' && info.size !== purpose.archive_size)
        throw new MacArchiveInspectionError(
          'refusal',
          'The Mac runtime archive size differs from its publication.',
        );
      if (info.size > ARCHIVE_MAX_COMPRESSED_BYTES)
        throw new AppValidationError('app archive exceeds compressed size limit');
      const parent = inspection ? tmpdir() : await resolved_path(dirname(target));
      require_live();
      if (!inspection) target = join(parent, basename(target));
      const ancestor = await existing_parent(parent);
      if (!inspection)
        await capacity(
          ancestor,
          BigInt(info.size) * 2n + BigInt(ARCHIVE_SPACE_RESERVE_BYTES),
          'app archive has insufficient free space before spooling',
        );
      work = await mkdtemp(join(ancestor, '.app-unpack-'));
      require_live();
      await chmod(work, 0o700);
      require_live();
      let release!: () => void;
      closed = new Promise<void>((resolve) => {
        release = resolve;
      });
      try {
        child = child_processes.spawn(
          process.execPath,
          [
            fileURLToPath(new URL('./archive_unpack_main.ts', import.meta.url)),
            JSON.stringify({
              spool_root: work,
              ...(!inspection ? { destination: target } : {}),
              ...purpose,
            }),
          ],
          {
            cwd: work,
            env: {
              PATH: '/usr/local/bin:/usr/bin:/bin',
              HOME: work,
              TMPDIR: work,
              LANG: 'C.UTF-8',
            },
            stdio: ['pipe', 'pipe', 'pipe', input.fd],
          },
        );
      } catch (error) {
        release();
        throw error;
      }
      child.once('close', (status) => {
        ended = true;
        code = status;
        release();
      });
      child.once('exit', () => {
        ended = true;
      });
      child.on('error', (error) => {
        failure ??= error;
      });
      child.stdin!.on('error', (error) => {
        failure ??= error;
      });
      child.stdout!.on('error', (error) => {
        failure ??= error;
      });
      let diagnostic = '';
      child.stderr!.on('data', (bytes: Buffer) => {
        diagnostic = (diagnostic + bytes.toString('utf8')).slice(-2000);
      });
      require_live();
      let validated = false;
      for await (const message of read_archive_messages(child.stdout!)) {
        if (failure !== undefined) throw failure;
        if (execution) assert_release_execution(execution);
        if (inspection && message.type === 'mac-inspection-error') throw inspection_error(message);
        if (
          inspection &&
          message.type === 'mac-inspection-result' &&
          result === undefined &&
          typeof message.python === 'boolean' &&
          typeof message.node === 'boolean' &&
          typeof message.entrypoint === 'boolean'
        ) {
          const bytes = message.manifest_bytes;
          if (!(
            bytes === null ||
            (typeof bytes === 'string' &&
              bytes.length <= Math.ceil(MAX_MAC_RUNTIME_MANIFEST_BYTES / 3) * 4 &&
              Buffer.from(bytes, 'base64').toString('base64') === bytes &&
              Buffer.from(bytes, 'base64').length <= MAX_MAC_RUNTIME_MANIFEST_BYTES)
          ))
            throw new Error('invalid Mac inspection manifest bytes');
          // Decode only after the exact child's actual close, below.
          result = {
            manifest: bytes,
            python: message.python,
            node: message.node,
            entrypoint: message.entrypoint,
          };
          continue;
        }
        if (inspection) throw new Error('invalid Mac inspection protocol');
        if (message.type === 'error') throw new AppValidationError(String(message.message));
        if (
          message.type === 'space' &&
          !validated &&
          Number.isSafeInteger(message.bytes) &&
          Number(message.bytes) > 0 &&
          Number(message.bytes) <= ARCHIVE_SPOOL_BATCH_BYTES
        ) {
          await capacity(
            work,
            BigInt(ARCHIVE_SPACE_RESERVE_BYTES) + BigInt(Number(message.bytes)),
            'app archive exhausted free-space reserve while spooling',
          );
        } else if (
          message.type === 'validated' &&
          !validated &&
          Number.isSafeInteger(message.expanded_bytes) &&
          Number(message.expanded_bytes) >= 0 &&
          Number(message.expanded_bytes) <= ARCHIVE_MAX_EXPANDED_BYTES
        ) {
          await capacity(
            work,
            BigInt(ARCHIVE_SPACE_RESERVE_BYTES) + BigInt(Number(message.expanded_bytes)),
            'app archive has insufficient free space for output',
          );
          require_live();
          await mkdir(parent, { recursive: true, mode: 0o700 });
          require_live();
          try {
            await mkdir(target, { mode: 0o700 });
            ownOutput = true;
          } catch (error) {
            if (error instanceof Error && 'code' in error && error.code === 'EEXIST')
              throw new AppValidationError('archive destination must not exist');
            throw error;
          }
          require_live();
          await chmod(target, 0o700);
          validated = true;
        } else if (
          purpose.purpose === 'application' &&
          message.type === 'result' &&
          validated &&
          result === undefined &&
          is_record(message.manifest)
        ) {
          result = message.manifest as AppManifest;
          continue;
        } else if (
          purpose.purpose === 'source-export' &&
          message.type === 'source-export-result' &&
          validated &&
          result === undefined &&
          typeof message.source_sha256 === 'string' &&
          /^[0-9a-f]{64}$/.test(message.source_sha256)
        ) {
          result = message.source_sha256;
          continue;
        } else throw new AppValidationError('app archive child protocol is invalid');
        require_live();
        await write_archive_message(child.stdin!, { ok: true });
      }
      await closed;
      if (execution) assert_release_execution(execution);
      if (failure !== undefined) throw failure;
      if (code !== 0 || !result)
        throw new AppValidationError(
          `app archive child failed${diagnostic ? ': ' + diagnostic : ''}`,
        );
      success = true;
    } catch (error) {
      cancel(error);
    } finally {
      const clean = async (operation: () => Promise<unknown>): Promise<void> => {
        try {
          await operation();
        } catch (error) {
          errors.push(error);
        }
      };
      if (closed) await clean(() => closed!);
      if (input) await clean(() => input!.close());
      if (!success && ownOutput) await clean(() => remove_owned_tree(target));
      if (work) await clean(() => remove_owned_tree(work!));
      if (success && execution && failure === undefined) {
        try {
          assert_release_execution(execution);
        } catch (error) {
          cancel(error);
        }
      }
      if (success && ownOutput && (errors.length || failure !== undefined))
        await clean(() => remove_owned_tree(target));
    }
    if (errors.length)
      throw new AppValidationError(
        `app archive cleanup failed; owned paths: ${work ?? ''} ${ownOutput ? target : ''}; ${errors.map(String).join('; ')}`,
      );
    if (failure !== undefined)
      throw inspection || failure instanceof AppValidationError
        ? failure
        : new AppValidationError(
            `app archive unpack failed: ${failure instanceof Error ? failure.message : String(failure)}`,
          );
    if (inspection) {
      const inspected = result as MacArchiveInspection;
      try {
        return {
          manifest:
            inspected.manifest === null
              ? null
              : decode_mac_manifest(Buffer.from(inspected.manifest as string, 'base64')),
          python: inspected.python,
          node: inspected.node,
          entrypoint: inspected.entrypoint,
        };
      } catch (error) {
        throw error instanceof MacArchiveJsonError ? error.original : error;
      }
    }
    return result!;
  }
  signal?.addEventListener('abort', abort);
  if (signal?.aborted) abort();
  const actual = unpack();
  try {
    return await actual;
  } finally {
    signal?.removeEventListener('abort', abort);
  }
}
