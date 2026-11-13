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
import { pipeline } from 'node:stream/promises';
import { Pack } from 'tar';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import { ShutdownDeadlineExceeded } from '../../lifecycle/contracts.ts';
import { stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import { inside } from '../ticket_scratch/logic.ts';
import type { AppManifest, ArchiveUnpackRequest, ReleaseExecution } from './contracts.ts';
import { assert_release_execution } from './process.ts';
import { AppValidationError } from './contracts.ts';
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
  if (inside(target, root))
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
): Promise<AppManifest> {
  return (await extract_archive(
    archive,
    destination,
    { purpose: 'application', archive_sha256, expected_sha },
    child_processes,
    read_free_space,
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
async function extract_archive(
  archive: string,
  destination: string,
  purpose:
    | Omit<Extract<ArchiveUnpackRequest, { purpose: 'application' }>, 'spool_root' | 'destination'>
    | { purpose: 'source-export' },
  child_processes: OwnedChildProcesses,
  read_free_space: (path: string) => Promise<bigint>,
  execution?: ReleaseExecution,
): Promise<AppManifest | string> {
  let input: FileHandle | undefined, work: string | undefined, child: ChildProcess | undefined;
  let closed: Promise<void> | undefined,
    ended = false,
    code: number | null = null;
  let ownOutput = false,
    success = false,
    result: AppManifest | string | undefined,
    failure: unknown;
  const errors: unknown[] = [];
  const cancel = (reason: unknown): void => {
    failure ??= reason;
    if (child && !ended) {
      child.stdin?.destroy();
      try {
        stop_one_shot_child(child);
      } catch (error) {
        errors.push(error);
      }
    }
  };
  const abort = () => cancel(execution!.signal.reason);
  let target = resolve(destination);
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
  async function unpack(): Promise<AppManifest | string> {
    try {
      require_live();
      input = await open(archive, constants.O_RDONLY | constants.O_NONBLOCK);
      require_live();
      const info = await input.stat();
      require_live();
      if (!info.isFile()) throw new AppValidationError('app archive must be a regular file');
      if (info.size > ARCHIVE_MAX_COMPRESSED_BYTES)
        throw new AppValidationError('app archive exceeds compressed size limit');
      const parent = await resolved_path(dirname(target));
      require_live();
      target = join(parent, basename(target));
      const ancestor = await existing_parent(parent);
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
            JSON.stringify({ spool_root: work, destination: target, ...purpose }),
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
      let diagnostic = '';
      child.stderr!.on('data', (bytes: Buffer) => {
        diagnostic = (diagnostic + bytes.toString('utf8')).slice(-2000);
      });
      require_live();
      let validated = false;
      for await (const message of read_archive_messages(child.stdout!)) {
        if (failure !== undefined) throw failure;
        if (execution) assert_release_execution(execution);
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
          message.manifest &&
          typeof message.manifest === 'object'
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
      failure = error;
      if (child && !ended)
        try {
          stop_one_shot_child(child);
        } catch (error) {
          errors.push(error);
        }
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
      throw failure instanceof AppValidationError
        ? failure
        : new AppValidationError(
            `app archive unpack failed: ${failure instanceof Error ? failure.message : String(failure)}`,
          );
    return result!;
  }
  execution?.signal.addEventListener('abort', abort);
  const actual = unpack();
  if (execution && execution.deadline !== null)
    void observe_shutdown(
      actual,
      { deadline: execution.deadline },
      'release operation exceeded its time budget',
    ).catch((error) => {
      if (error instanceof ShutdownDeadlineExceeded)
        cancel(new AppValidationError('release operation exceeded its time budget'));
    });
  try {
    return await actual;
  } finally {
    execution?.signal.removeEventListener('abort', abort);
  }
}
