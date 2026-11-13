/** Verified transfer custody. The served tree is published later by the inbox. */
import { createHash, randomUUID } from 'node:crypto';
import type { FileHandle } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, relative } from 'node:path';
import { PlannerError } from '../core/contracts.ts';
import { stringify_python_json } from '../core/http/numeric_json.ts';
import { resolve_path_sync } from '../core/path_resolution.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import type { ConversationMessageFiles } from '../conversation/message_files.ts';
import { MessageFileMissing } from '../conversation/record/message_files.ts';
import { is_shutdown_business_cancellation, observe_shutdown } from '../conversation/shutdown.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type {
  FileOperationOptions,
  HeldFileLocation,
  InputFile,
  UploadSource,
  VpsFileInbox,
  VpsFilesOptions,
  WireValue,
} from './contracts.ts';
import type { WireInteger } from './wire.generated.ts';
import type { SqliteCommandMailbox } from './vps_mailbox.ts';
import { FILE_CHUNK_BYTES, FILE_TRANSFER_STALL_SECONDS } from './configuration.ts';
import {
  FileCleanupError,
  assert_file_support,
  contained_path,
  create_file_at,
  descriptor_hash,
  directory_descriptor,
  install_verified,
  regular_descriptor,
  safe_component,
  unlink_at,
} from './files.ts';
import { as_object, integer_value, ValueError } from './protocol.ts';
import { normalize_integer } from './integers.ts';

function aborted(signal: AbortSignal): unknown {
  return is_shutdown_business_cancellation(signal.reason)
    ? signal.reason
    : Object.assign(new DOMException('Executor file transfer aborted.', 'AbortError'), {
        cause: signal.reason,
      });
}
function check(signal: AbortSignal): void {
  if (signal.aborted) throw aborted(signal);
}
function failure(errors: readonly unknown[]): unknown {
  return errors.length === 1
    ? errors[0]
    : new AggregateError(errors, 'Executor file operation and cleanup failed.');
}
function missing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, failed) => {
    resolve = done;
    reject = failed;
  });
  return { promise, resolve, reject };
}

/** Stateless physical staging, supplied to the inbox before the transfer owner is built. */
export class FileStaging {
  readonly staging: string;
  private readonly tickets: string;

  constructor(db_path: string) {
    const root = dirname(resolve_path_sync(db_path));
    this.staging = join(root, 'executor-uploads');
    this.tickets = join(root, 'files', 'tickets');
  }
  async file_target(manifest: WireValue<'FileManifest'>): Promise<string> {
    safe_component(manifest.owner_id);
    if (
      integer_value(manifest.byte_count) < 0n ||
      manifest.sha256.length !== 64 ||
      /[^0-9a-f]/.test(manifest.sha256)
    )
      throw new ValueError('invalid file length or digest');
    if (manifest.scope !== 'ticket') throw new ValueError('Mac uploads must belong to a Ticket');
    return contained_path(this.tickets, `${manifest.owner_id}/${manifest.relative_path}`);
  }
  async staged_file(manifest: WireValue<'FileManifest'>): Promise<string> {
    await this.file_target(manifest);
    const record = stringify_python_json(as_object(manifest, 'FileManifest'), {
      sort_keys: true,
      compact: true,
    });
    return join(this.staging, createHash('sha256').update(record).digest('hex'));
  }
  async store_upload(
    manifest: WireValue<'FileManifest'>,
    source: HeldFileLocation,
    options: FileOperationOptions = {},
  ): Promise<void> {
    const path = await this.staged_file(manifest);
    const parent = await directory_descriptor(dirname(path), { create: true });
    const errors: unknown[] = [];
    try {
      await install_verified(source, { parent, leaf: basename(path), path }, manifest, options);
    } catch (error) {
      errors.push(error);
    }
    try {
      await parent.close();
    } catch (error) {
      const cleanup_errors = errors.flatMap((earlier) =>
        earlier instanceof FileCleanupError ? earlier.cleanup_errors : [],
      );
      const current = error instanceof FileCleanupError ? error.cleanup_errors : [error];
      throw new FileCleanupError([...errors, error], [...cleanup_errors, ...current]);
    }
    if (errors.length) throw failure(errors);
  }
}

export class VpsFiles {
  readonly staging: string;
  private readonly inbox: VpsFileInbox;
  private readonly mailbox: Pick<SqliteCommandMailbox, 'command_for_input'>;
  private readonly message_files: ConversationMessageFiles;
  private readonly message_root: string;
  private readonly media_signal: AbortSignal;
  private readonly operations = new Set<Promise<unknown>>();
  private readonly cleanup_failures = new Set<unknown>();
  private readonly actual = deferred<void>();
  private sealed = false;

  constructor(
    db_path: string,
    inbox: VpsFileInbox,
    mailbox: Pick<SqliteCommandMailbox, 'command_for_input'>,
    message_files: ConversationMessageFiles,
    options: VpsFilesOptions,
  ) {
    this.staging = join(dirname(resolve_path_sync(db_path)), 'executor-uploads');
    this.inbox = inbox;
    this.mailbox = mailbox;
    this.message_files = message_files;
    this.message_root = join(resolve_path_sync(dirname(db_path)), 'files', 'conversations');
    this.media_signal = options.signal;
    void this.actual.promise.catch(() => {});
  }
  private admit(options: FileOperationOptions): AbortSignal {
    if (this.sealed) throw new ServerLifecycleError('Executor file transfers are stopped.');
    const signal = options.signal
      ? AbortSignal.any([this.media_signal, options.signal])
      : this.media_signal;
    check(signal);
    return signal;
  }
  private retain<T>(promise: Promise<T>): Promise<T> {
    this.operations.add(promise);
    void promise.then(
      () => this.operations.delete(promise),
      (error) => {
        this.remember_cleanup(error);
        this.operations.delete(promise);
      },
    );
    return promise;
  }
  private remember_cleanup(error: unknown): void {
    if (error instanceof FileCleanupError)
      for (const cleanup of error.cleanup_errors) this.cleanup_failures.add(cleanup);
  }
  private observe<T>(
    promise: Promise<T>,
    signal: AbortSignal,
    stalled?: (error: PlannerError) => void,
  ): Promise<T> {
    const observed = new Promise<T>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      let done = false;
      const finish = (outcome: { value: T } | { error: unknown }) => {
        if (done) return;
        done = true;
        const errors: unknown[] = [];
        try {
          clearTimeout(timer);
        } catch (error) {
          this.cleanup_failures.add(error);
          errors.push(error);
        }
        try {
          signal.removeEventListener('abort', abort);
        } catch (error) {
          this.cleanup_failures.add(error);
          errors.push(error);
        }
        if (errors.length) {
          if ('error' in outcome) errors.unshift(outcome.error);
          reject(failure(errors));
        } else if ('error' in outcome) reject(outcome.error);
        else resolve(outcome.value);
      };
      const abort = () => finish({ error: aborted(signal) });
      void promise.then(
        (value) => finish({ value }),
        (error) => finish({ error }),
      );
      try {
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) abort();
        else if (stalled)
          timer = setTimeout(() => {
            const error = new PlannerError(
              'retry_later',
              'the upload stopped moving; send it again',
            );
            stalled(error);
            finish({ error });
          }, FILE_TRANSFER_STALL_SECONDS * 1000);
      } catch (error) {
        finish({ error });
      }
    });
    return this.retain(observed);
  }
  private start<T>(
    signal: AbortSignal,
    work: (reply: ReturnType<typeof deferred<T>>) => Promise<void>,
  ): Promise<T> {
    const reply = deferred<T>();
    const physical = deferred<void>();
    this.retain(physical.promise);
    void physical.promise.catch(reply.reject);
    try {
      physical.resolve(work(reply));
    } catch (error) {
      physical.reject(error);
    }
    return this.observe(reply.promise, signal);
  }
  async upload(
    identity: ExecutorIdentity,
    sha256: string,
    upload: WireValue<'FileUploadRequest'>,
    content_length: WireInteger | null,
    chunks: UploadSource,
    options: FileOperationOptions = {},
  ): Promise<void> {
    void identity; // The route admits the executor; the participant checks its child's assignment.
    const signal = this.admit(options);
    return this.start<void>(signal, async (reply) => {
      const manifest = upload.manifest;
      const budget = integer_value(manifest.byte_count);
      if (
        manifest.sha256 !== sha256 ||
        content_length === null ||
        integer_value(content_length) !== budget
      )
        throw new PlannerError('validation', 'upload does not match its manifest');
      try {
        await this.inbox.authorize_upload(upload);
      } catch (error) {
        this.remember_cleanup(error);
        if (error instanceof ValueError) throw new PlannerError('validation', error.message);
        throw error;
      }
      check(signal);
      await assert_file_support();
      const parent = await directory_descriptor(this.staging, { create: true });
      const leaf = '.incoming-' + randomUUID().replaceAll('-', '');
      const source = { parent, leaf, path: join(this.staging, leaf) };
      let output: FileHandle | undefined;
      let created = false;
      let iterator: AsyncIterator<Uint8Array> | undefined;
      let pending: Promise<IteratorResult<Uint8Array>> | undefined;
      let ended = false;
      const errors: unknown[] = [];
      try {
        check(signal);
        output = await create_file_at(source);
        created = true;
        let count = 0n;
        const digest = createHash('sha256');
        iterator = chunks[Symbol.asyncIterator]();
        while (true) {
          check(signal);
          pending = Promise.resolve(iterator.next());
          const next = await this.observe(pending, signal, reply.reject);
          pending = undefined;
          if (next.done) {
            ended = true;
            break;
          }
          const chunk = next.value;
          if (count + BigInt(chunk.byteLength) > budget)
            throw new PlannerError('validation', 'upload exceeds its manifest');
          for (let offset = 0; offset < chunk.byteLength; offset += FILE_CHUNK_BYTES) {
            const piece = chunk.subarray(offset, offset + FILE_CHUNK_BYTES);
            let written = 0;
            while (written < piece.byteLength) {
              check(signal);
              const result = await output.write(piece, written, piece.byteLength - written);
              if (result.bytesWritten <= 0)
                throw Object.assign(new Error('short file write'), { code: 'EIO' });
              written += result.bytesWritten;
            }
            digest.update(piece);
            count += BigInt(piece.byteLength);
          }
        }
        if (count !== budget || digest.digest('hex') !== manifest.sha256)
          throw new PlannerError('validation', 'upload is short or corrupt');
        check(signal);
        await output.sync();
      } catch (error) {
        this.remember_cleanup(error);
        errors.push(error);
      }
      // Destroy the owned source before iterator return, which can queue behind next().
      // Release the local file before joining the source's actual cancellation.
      let returned: Promise<IteratorResult<Uint8Array>> | undefined;
      if (!ended && iterator) {
        try {
          chunks.destroy(new DOMException('Executor upload cancelled.', 'AbortError'));
        } catch (error) {
          this.remember_cleanup(error);
          this.cleanup_failures.add(error);
          if (!errors.includes(error)) errors.push(error);
        }
      }
      if (!ended && iterator?.return) {
        try {
          returned = Promise.resolve(iterator.return());
          void returned.catch(() => {});
        } catch (error) {
          this.remember_cleanup(error);
          if (!is_shutdown_business_cancellation(error)) {
            this.cleanup_failures.add(error);
            if (!errors.includes(error)) errors.push(error);
          }
        }
      }
      if (output) {
        try {
          await output.close();
        } catch (error) {
          this.cleanup_failures.add(error);
          errors.push(error);
        }
      }
      if (!errors.length) {
        try {
          check(signal);
          await this.inbox.store_upload(upload, source, { signal });
        } catch (error) {
          this.remember_cleanup(error);
          errors.push(
            error instanceof ValueError ? new PlannerError('validation', error.message) : error,
          );
        }
      }
      try {
        if (created) await unlink_at(parent, leaf, { missing_ok: true });
      } catch (error) {
        this.cleanup_failures.add(error);
        errors.push(error);
      }
      try {
        await parent.close();
      } catch (error) {
        this.cleanup_failures.add(error);
        errors.push(error);
      }
      // Retain the source's real work after releasing our incoming inode and descriptors.
      if (pending) {
        const [result] = await Promise.allSettled([pending]);
        if (result.status === 'rejected') this.remember_cleanup(result.reason);
        if (
          result.status === 'rejected' &&
          !errors.includes(result.reason) &&
          !is_shutdown_business_cancellation(result.reason)
        ) {
          this.cleanup_failures.add(result.reason);
          errors.push(result.reason);
        }
      }
      if (returned) {
        try {
          await returned;
        } catch (error) {
          this.remember_cleanup(error);
          if (!is_shutdown_business_cancellation(error)) {
            this.cleanup_failures.add(error);
            if (!errors.includes(error)) errors.push(error);
          }
        }
      }
      if (errors.length) throw failure(errors);
      reply.resolve();
    });
  }
  async input_file(
    identity: ExecutorIdentity,
    command_id: string,
    stored_file_id: string,
    options: FileOperationOptions = {},
  ): Promise<InputFile> {
    const signal = this.admit(options);
    return this.start<InputFile>(signal, async (reply) => {
      const command = this.mailbox.command_for_input(command_id);
      const absent = new PlannerError('not_found', 'no such input for this command');
      if (!command || command.child.executor_incarnation !== identity.executor_incarnation)
        throw absent;
      const args = command.arguments;
      const allowed =
        (args.kind === 'write_prompt' || args.kind === 'steer') &&
        [...args.content, ...(args.sender_content ?? [])].some(
          (piece) => 'byte_count' in piece && piece.stored_file_id === stored_file_id,
        );
      if (!allowed) throw absent;
      await this.inbox.runs.authority(command.child);
      check(signal);
      let incoming: FileHandle | undefined;
      try {
        try {
          safe_component(command.child.conversation_id);
          safe_component(stored_file_id);
        } catch (error) {
          if (error instanceof ValueError) throw absent;
          throw error;
        }
        const path = this.message_files.path_of(command.child.conversation_id, stored_file_id);
        const within = relative(join(this.message_root, command.child.conversation_id), path);
        if (within === '..' || within.startsWith('../') || isAbsolute(within)) throw absent;
        await assert_file_support();
        incoming = await regular_descriptor(path);
        const [sha256, count] = await descriptor_hash(incoming, { signal });
        check(signal);
        const held = this.input_stream(incoming, path, sha256, count, signal);
        incoming = undefined;
        reply.resolve(held.input);
        await held.finished;
      } catch (error) {
        this.remember_cleanup(error);
        const unsafe_link =
          error instanceof Error &&
          'code' in error &&
          (error.code === 'ELOOP' || error.code === 'ENOTDIR');
        const errors = [
          error instanceof MessageFileMissing || missing(error) || unsafe_link ? absent : error,
        ];
        if (incoming) {
          try {
            await incoming.close();
          } catch (cleanup) {
            this.cleanup_failures.add(cleanup);
            errors.push(cleanup);
          }
        }
        throw failure(errors);
      }
    });
  }
  private input_stream(
    incoming: FileHandle,
    path: string,
    sha256: string,
    count: bigint,
    signal: AbortSignal,
  ): { input: InputFile; finished: Promise<void> } {
    const finished = deferred<void>();
    void finished.promise.catch(() => {});
    const failures = new Set<unknown>();
    let reading: Promise<unknown> | undefined;
    let closing = false;
    let used = false;
    let cancellation: unknown;
    const cancellation_error = () => (cancellation ??= aborted(signal));
    const check_input = () => {
      if (signal.aborted) throw cancellation_error();
    };
    const closed = new DOMException('Executor input file is closed.', 'AbortError');
    const close = (reason?: unknown): Promise<void> => {
      if (reason !== undefined) failures.add(reason);
      if (closing) return finished.promise;
      closing = true;
      const cleanup = async () => {
        if (reading) {
          const [result] = await Promise.allSettled([reading]);
          if (result.status === 'rejected') failures.add(result.reason);
        }
        try {
          await incoming.close();
        } catch (error) {
          this.cleanup_failures.add(error);
          failures.add(error);
        }
        try {
          signal.removeEventListener('abort', abort);
        } catch (error) {
          this.cleanup_failures.add(error);
          failures.add(error);
        }
        if (failures.size) throw failure([...failures]);
      };
      void cleanup().then(finished.resolve, finished.reject);
      return finished.promise;
    };
    const abort = () => {
      void close(cancellation_error()).catch(() => {});
    };
    const read_chunks = async function* () {
      let position = 0n;
      try {
        while (position < count) {
          check_input();
          if (closing) throw closed;
          const remaining = count - position;
          const buffer = Buffer.allocUnsafe(
            remaining < BigInt(FILE_CHUNK_BYTES) ? Number(remaining) : FILE_CHUNK_BYTES,
          );
          const pending = incoming.read(buffer, 0, buffer.byteLength, position);
          reading = pending;
          let bytesRead: number;
          try {
            ({ bytesRead } = await pending);
          } finally {
            reading = undefined;
          }
          check_input();
          if (closing) throw closed;
          if (!bytesRead)
            throw Object.assign(new Error('input file is shorter than its digest'), {
              code: 'EIO',
            });
          position += BigInt(bytesRead);
          yield buffer.subarray(0, bytesRead);
        }
      } catch (error) {
        failures.add(error);
        throw error;
      } finally {
        await close();
      }
    };
    const chunks: AsyncIterable<Uint8Array> = {
      [Symbol.asyncIterator]() {
        if (used) throw new ValueError('input file permits only one consumer');
        used = true;
        const iterator = read_chunks();
        return {
          next: () => iterator.next(),
          async return() {
            try {
              return await iterator.return();
            } finally {
              await close();
            }
          },
        };
      },
    };
    try {
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    } catch (error) {
      void close(error).catch(() => {});
    }
    return {
      input: { path, sha256, byte_count: normalize_integer(count), chunks, close: () => close() },
      finished: finished.promise,
    };
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.sealed) {
      this.sealed = true;
      const pending = [...this.operations];
      void this.join(pending).then(this.actual.resolve, this.actual.reject);
    }
    return observe_shutdown(
      this.actual.promise,
      options,
      'Executor file transfers remain unfinished at the shutdown deadline.',
    );
  }
  private async join(first: readonly Promise<unknown>[]): Promise<void> {
    const failures = new Set<unknown>();
    const seen = new Set<Promise<unknown>>();
    let pending = first;
    while (pending.length) {
      for (const operation of pending) seen.add(operation);
      for (const result of await Promise.allSettled(pending))
        if (result.status === 'rejected' && !is_shutdown_business_cancellation(result.reason))
          failures.add(result.reason);
      pending = [...this.operations].filter((operation) => !seen.has(operation));
    }
    for (const error of this.cleanup_failures) failures.add(error);
    if (failures.size)
      throw new AggregateError([...failures], 'Executor file transfer shutdown failed.');
  }
  settled(): Promise<void> {
    return this.actual.promise;
  }
}
