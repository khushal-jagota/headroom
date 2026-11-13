/** Retain canonical bodies off main; each persistence retry opens a fresh bounded reader. */
import { parentPort } from 'node:worker_threads';
import { as_object, encode_value } from '../../protocol.ts';
import { stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import type { BackendSnapshot } from '../../../conversation/snapshot/contracts.ts';
import { unpack } from '../storage/journal_worker_contracts.ts';
import { FILE_CHUNK_BYTES } from '../configuration.ts';
import type { CatalogAction, CatalogWorkerRequest, CatalogWorkerReply } from './contracts.ts';
if (!parentPort) throw new Error('Mac catalog encoder requires its owner port.');
const port = parentPort,
  results = new Map<number, Buffer>(),
  readers = new Map<number, { result: number; offset: number }>();
let sequence = 0;
function execute(action: CatalogAction): unknown {
  if (action.operation === 'encode') {
    const snapshot = unpack(action.snapshot) as BackendSnapshot | null;
    const model_list = snapshot === null ? null : as_object(snapshot, 'BackendSnapshot');
    const bytes = Buffer.from(
      stringify_pydantic_json(
        encode_value(
          { command_id: action.command_id, child_outcome: null, model_list },
          'CommandResult',
        ),
      ),
    );
    const result = ++sequence;
    results.set(result, bytes);
    return { result, byte_count: bytes.length };
  }
  if (action.operation === 'close_result') {
    results.delete(action.result);
    for (const [id, value] of readers) if (value.result === action.result) readers.delete(id);
    return;
  }
  if (action.operation === 'open') {
    const bytes = results.get(action.result);
    if (!bytes) throw new Error('Catalog result is closed.');
    const reader = ++sequence;
    readers.set(reader, { result: action.result, offset: 0 });
    return { reader, byte_count: bytes.length };
  }
  if (action.operation === 'close_reader') {
    readers.delete(action.reader);
    return;
  }
  const reader = readers.get(action.reader),
    bytes = reader === undefined ? undefined : results.get(reader.result);
  if (!reader || !bytes) throw new Error('Catalog reader is closed.');
  if (reader.offset === bytes.length) {
    readers.delete(action.reader);
    return null;
  }
  const chunk = Uint8Array.from(
    bytes.subarray(reader.offset, reader.offset + FILE_CHUNK_BYTES),
  ).buffer;
  reader.offset += chunk.byteLength;
  return chunk;
}
port.on('message', (request: CatalogWorkerRequest) => {
  if ('stop' in request) {
    readers.clear();
    results.clear();
    port.close();
    return;
  }
  try {
    const value = execute(request.action);
    port.postMessage(
      { id: request.id, value } satisfies CatalogWorkerReply,
      value instanceof ArrayBuffer ? [value] : [],
    );
  } catch (error) {
    port.postMessage({
      id: request.id,
      error: error instanceof Error ? error.message : String(error),
    } satisfies CatalogWorkerReply);
  }
});
