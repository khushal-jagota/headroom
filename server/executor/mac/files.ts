/** File work belongs to this explicitly owned thread, never the service event loop. */
import { Worker } from 'node:worker_threads';
import { observe_shutdown } from '../../conversation/shutdown.ts';
import { ServerLifecycleError } from '../../lifecycle/contracts.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
import type {
  DirectoryHandle,
  FileOperation,
  FileOperationArguments,
  FileOperationResults,
  FileWorkerRequest,
  FileWorkerResponse,
} from './file_worker_contracts.ts';
export { contained_path, file_version, safe_component } from './file_metadata.ts';
export type { FileVersion } from './file_metadata.ts';
export type { DirectoryHandle } from './file_worker_contracts.ts';

export class FilesService {
  private worker: Worker | undefined;
  private exit: Promise<void> = Promise.resolve();
  private sequence = 0;
  private closed = false;
  private failure: Error | undefined;
  private stop_requested = false;
  private readonly failures = new Set<(error: Error) => void>();
  private readonly directories = new WeakSet<DirectoryHandle>();
  private readonly pending = new Map<
    number,
    { resolve(value: unknown): void; reject(error: Error): void }
  >();
  private seal(error: Error): void {
    const first = this.failure === undefined;
    this.failure ??= error;
    this.closed = true;
    for (const request of this.pending.values()) request.reject(error);
    this.pending.clear();
    if (first) for (const listener of this.failures) listener(error);
  }
  private start(): Worker {
    if (this.closed)
      throw this.failure ?? new ServerLifecycleError('Executor files service is stopped.');
    if (this.worker !== undefined) return this.worker;
    // Node 22.18+ and 24 run these .ts files directly. No inherited eval or loader is needed.
    const worker = new Worker(new URL('./files_worker.ts', import.meta.url), {
      execArgv: [],
      // libc opens bypass Node's tracker. This worker explicitly owns every descriptor.
      trackUnmanagedFds: false,
    });
    this.worker = worker;
    this.exit = new Promise<void>((resolve) => worker.once('exit', () => resolve()));
    worker.on('message', (message: FileWorkerResponse) => {
      const request = this.pending.get(message.id);
      this.pending.delete(message.id);
      if ('error' in message)
        request?.reject(Object.assign(new Error(message.error.message), message.error));
      else request?.resolve(message.value);
    });
    worker.on('error', (error) => this.seal(error));
    worker.on('exit', (code) => {
      if (this.pending.size !== 0 || !this.closed)
        this.seal(new Error(`Executor files worker exited (${code}).`));
      this.worker = undefined;
    });
    return worker;
  }
  private request<Operation extends FileOperation>(
    operation: Operation,
    arguments_: FileOperationArguments[Operation],
  ): Promise<FileOperationResults[Operation]> {
    return new Promise((resolve, reject) => {
      if (this.closed) {
        reject(this.failure ?? new ServerLifecycleError('Executor files service is stopped.'));
        return;
      }
      const id = ++this.sequence;
      try {
        const worker = this.start();
        this.pending.set(id, {
          resolve: (value) => resolve(value as FileOperationResults[Operation]),
          reject,
        });
        worker.postMessage({ id, operation, arguments: arguments_ } as FileWorkerRequest);
      } catch (error) {
        const failure = error instanceof Error ? error : new Error(String(error));
        this.seal(failure);
        reject(failure);
        this.request_stop();
      }
    });
  }
  file_digest(path: string) {
    return this.request('file_digest', [path]);
  }
  verified_version(path: string, sha256: string, byte_count: number) {
    return this.request('verified_version', [path, sha256, byte_count]);
  }
  copy_verified(source: string, target: string, sha256: string, byte_count: number) {
    return this.request('copy_verified', [source, target, sha256, byte_count]);
  }
  install_verified(source: string, target: string, sha256: string, byte_count: number) {
    return this.request('install_verified', [source, target, sha256, byte_count]);
  }
  fsync_directory(path: string) {
    return this.request('fsync_directory', [path]);
  }
  assert_healthy(): void {
    if (this.closed || this.worker?.threadId === -1)
      throw this.failure ?? new ServerLifecycleError('Executor file worker is unavailable.');
  }
  on_failure(listener: (error: Error) => void): () => void {
    this.failures.add(listener);
    if (this.failure !== undefined) listener(this.failure);
    return () => this.failures.delete(listener);
  }
  async capture_file(source: string, blobs: DirectoryHandle, checked_ns: bigint) {
    return this.request('capture_file', [source, this.directory(blobs), checked_ns]);
  }
  async sweep_directory(handle: DirectoryHandle, path: string, keep: readonly string[]) {
    return this.request('sweep_directory', [this.directory(handle), path, keep]);
  }
  async open_directory(path: string, options: { create?: boolean } = {}): Promise<DirectoryHandle> {
    const handle = Object.freeze({ id: await this.request('open_directory', [path, options]) });
    this.directories.add(handle);
    return handle;
  }
  private directory(handle: DirectoryHandle): number {
    if (!this.directories.has(handle))
      throw new Error('Executor directory handle belongs to another files service or is closed.');
    return handle.id;
  }
  async close_directory(handle: DirectoryHandle): Promise<void> {
    await this.request('close_directory', [this.directory(handle)]);
    this.directories.delete(handle);
  }
  async copy_verified_at(
    source: string,
    handle: DirectoryHandle,
    name: string,
    sha256: string,
    byte_count: number,
  ): Promise<void> {
    await this.request('copy_verified_at', [
      source,
      this.directory(handle),
      name,
      sha256,
      byte_count,
    ]);
  }
  async install_verified_at(
    source: string,
    handle: DirectoryHandle,
    name: string,
    sha256: string,
    byte_count: number,
  ): Promise<void> {
    await this.request('install_verified_at', [
      source,
      this.directory(handle),
      name,
      sha256,
      byte_count,
    ]);
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.request_stop();
    return observe_shutdown(
      this.exit,
      options,
      'Executor file work remains unfinished at the shutdown deadline.',
    );
  }
  private request_stop(): void {
    if (this.stop_requested || this.worker === undefined) return;
    try {
      this.worker.postMessage({ operation: 'stop' } satisfies FileWorkerRequest);
      this.stop_requested = true;
    } catch (error) {
      // Leave this retryable. A failed send is not evidence that the thread exited.
      this.seal(error instanceof Error ? error : new Error(String(error)));
    }
  }
  settled(): Promise<void> {
    return this.exit;
  }
}
