/** Owns the home's file hashing thread. A failed thread rejects its waiters and restarts on demand. */
import { Worker } from 'node:worker_threads';

type Reply = { id: number; sha256: string; byte_count: number } | { id: number; error: string };
interface Waiter {
  resolve(value: readonly [string, number]): void;
  reject(error: Error): void;
}
export class FileHasher {
  private worker: Worker | undefined;
  private sequence = 0;
  private readonly waiting = new Map<number, Waiter>();

  /** The path must name a held inode, such as a leaf below an open directory descriptor. */
  digest(path: string): Promise<readonly [string, number]> {
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      this.waiting.set(id, { resolve, reject });
      try {
        this.start().postMessage({ id, path });
      } catch (error) {
        this.waiting.delete(id);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }
  private start(): Worker {
    if (this.worker) return this.worker;
    // Node 22.18+ and 24 run this .ts file directly.
    const worker = new Worker(new URL('./file_hash_worker.ts', import.meta.url), { execArgv: [] });
    worker.unref();
    const ended = (error: Error) => {
      if (this.worker === worker) this.worker = undefined;
      for (const waiter of this.waiting.values()) waiter.reject(error);
      this.waiting.clear();
    };
    worker.on('message', (reply: Reply) => {
      const waiter = this.waiting.get(reply.id);
      this.waiting.delete(reply.id);
      if ('error' in reply) waiter?.reject(new Error(reply.error));
      else waiter?.resolve([reply.sha256, reply.byte_count]);
    });
    worker.on('error', ended);
    worker.on('exit', () => ended(new Error('The file hashing thread stopped.')));
    this.worker = worker;
    return worker;
  }
  async stop(): Promise<void> {
    await this.worker?.terminate();
  }
}
