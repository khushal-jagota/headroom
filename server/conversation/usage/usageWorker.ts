/** All transcript records and scan-cache serialization remain in this owned worker. */
import { parentPort, workerData } from 'node:worker_threads';
import { UsageEngine } from './UsageEngine.ts';
import type {
  UsageWorkerData,
  UsageWorkerError,
  UsageWorkerRequest,
  UsageWorkerResponse,
} from './workerProtocol.ts';

const port = parentPort!;
const data = workerData as UsageWorkerData;
let sequence = 0;
const callbacks = new Map<number, { resolve(value: unknown): void; reject(error: Error): void }>();
function send(message: UsageWorkerResponse): void {
  port.postMessage(message);
}
function callback(kind: 'rates' | 'clock', signal?: AbortSignal): Promise<unknown> {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const abort = () => {
      callbacks.delete(id);
      send({ kind: 'cancel-callback', id });
      reject(signal!.reason);
    };
    callbacks.set(id, {
      resolve(value) {
        signal?.removeEventListener('abort', abort);
        resolve(value);
      },
      reject(error) {
        signal?.removeEventListener('abort', abort);
        reject(error);
      },
    });
    signal?.addEventListener('abort', abort, { once: true });
    send({ kind: 'callback', id, callback: kind });
  });
}
const engine = new UsageEngine(
  data.stateDir,
  [],
  {
    ratesFetcher: data.injectedRates ? (options) => callback('rates', options?.signal) : undefined,
  },
  data.injectedClock ? async () => Number(await callback('clock')) : undefined,
);
function errorBody(error: unknown): UsageWorkerError {
  const value = error instanceof Error ? error : new Error(String(error));
  return {
    name: value.name,
    message: value.message,
    ...('reason' in value ? { reason: String(value.reason) } : {}),
    ...('detail' in value ? { detail: String(value.detail) } : {}),
  };
}
port.on('message', (message: UsageWorkerRequest) => {
  if (message.kind === 'callback-result') {
    const pending = callbacks.get(message.id);
    callbacks.delete(message.id);
    if (message.error) pending?.reject(new Error(message.error.message));
    else pending?.resolve(message.value);
    return;
  }
  const task =
    message.kind === 'summary'
      ? engine.readSummary(message.input, message.settings, message.directories)
      : engine.refreshRates();
  void task.then(
    (value) => send({ kind: 'result', id: message.id, value }),
    (error: unknown) => send({ kind: 'error', id: message.id, error: errorBody(error) }),
  );
});
