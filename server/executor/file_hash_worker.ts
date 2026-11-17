/** Hashes one held file per request, so large uploads never occupy the server's main thread. */
import { createHash } from 'node:crypto';
import { closeSync, constants, openSync, readSync } from 'node:fs';
import { parentPort } from 'node:worker_threads';
import { FILE_CHUNK_BYTES } from './configuration.ts';

parentPort!.on('message', ({ id, path }: { id: number; path: string }) => {
  try {
    const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const hash = createHash('sha256');
      const buffer = Buffer.allocUnsafe(FILE_CHUNK_BYTES);
      let byte_count = 0;
      for (;;) {
        const read = readSync(descriptor, buffer, 0, buffer.length, null);
        if (read === 0) break;
        hash.update(buffer.subarray(0, read));
        byte_count += read;
      }
      parentPort!.postMessage({ id, sha256: hash.digest('hex'), byte_count });
    } finally {
      closeSync(descriptor);
    }
  } catch (error) {
    parentPort!.postMessage({ id, error: error instanceof Error ? error.message : String(error) });
  }
});
