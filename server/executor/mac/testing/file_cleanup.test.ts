/** Close failure must retain both the native capability and the worker's actual join. */
import { expect, test, vi } from 'vitest';
import { closeSync, openSync, mkdtempSync, rmSync, constants, fstatSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { close_retained } from '../retained_cleanup.ts';
import { FilesService } from '../files.ts';
import { ShutdownDeadlineExceeded } from '../../../lifecycle/contracts.ts';
import type { FileWorkerRequest } from '../file_worker_contracts.ts';

vi.mock('node:worker_threads', async () => {
  const { EventEmitter } = await import('node:events');
  return {
    Worker: class extends EventEmitter {
      private attempts = 0;
      postMessage(message: FileWorkerRequest): void {
        if (message.operation === 'close_transfer') {
          queueMicrotask(() =>
            this.emit('message', {
              cleanup_failed: {
                name: 'AggregateError',
                message: 'transfer close failed',
                errors: [
                  { name: 'Error', message: 'original operation' },
                  { name: 'Error', message: 'raw native close' },
                ],
              },
            }),
          );
        } else if (message.operation === 'stop') {
          this.attempts++;
          queueMicrotask(() =>
            this.attempts === 1
              ? this.emit('message', {
                  cleanup_failed: { name: 'Error', message: 'retained close failed' },
                })
              : this.emit('exit', 0),
          );
        } else queueMicrotask(() => this.emit('message', { id: message.id, value: 1 }));
      }
    },
  };
});

test('ordinary transfer close failure faults the host, retains both errors, and permits native cleanup retry', async () => {
  const files = new FilesService(),
    failures: Error[] = [];
  files.on_failure((error) => failures.push(error));
  const handle = await files.open_transfer('/synthetic', 'read');
  await expect(files.close_transfer(handle)).rejects.toMatchObject({
    name: 'AggregateError',
    errors: [{ message: 'original operation' }, { message: 'raw native close' }],
  });
  expect(failures).toHaveLength(1);
  await expect(files.read_transfer(handle)).rejects.toThrow('transfer close failed');
  await expect(files.stop({ deadline: performance.now() / 1000 + 0.03 })).rejects.toBeInstanceOf(
    ShutdownDeadlineExceeded,
  );
  await expect(files.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toBe(failures[0]);
  await expect(files.settled()).rejects.toBe(failures[0]);
});

test('a native close failure retains the descriptor and retries only that capability', () => {
  const root = mkdtempSync(join(tmpdir(), 'mac-close-'));
  const descriptor = openSync(join(root, 'file'), constants.O_CREAT | constants.O_RDWR, 0o600);
  const retained = new Map([[1, descriptor]]),
    failed = new Error('native close refused');
  try {
    expect(
      close_retained(retained, () => {
        throw failed;
      }),
    ).toBe(failed);
    expect(retained.get(1)).toBe(descriptor);
    expect(fstatSync(descriptor).isFile()).toBe(true);
    expect(close_retained(retained, closeSync)).toBeUndefined();
    expect(retained.size).toBe(0);
  } finally {
    if (retained.size) closeSync(descriptor);
    rmSync(root, { recursive: true, force: true });
  }
});

test('failed worker cleanup keeps actual settlement pending and later stop retries with its failure retained', async () => {
  const files = new FilesService();
  await files.open_directory('/synthetic');
  let actual = false;
  const joined = files.settled().finally(() => {
    actual = true;
  });
  void joined.catch(() => {});
  await expect(files.stop({ deadline: performance.now() / 1000 + 0.03 })).rejects.toBeInstanceOf(
    ShutdownDeadlineExceeded,
  );
  expect(actual).toBe(false);
  await expect(files.open_directory('/later')).rejects.toThrow('retained close failed');
  await expect(files.stop({ deadline: performance.now() / 1000 + 1 })).rejects.toThrow(
    'retained close failed',
  );
  await expect(joined).rejects.toThrow('retained close failed');
  expect(actual).toBe(true);
});
