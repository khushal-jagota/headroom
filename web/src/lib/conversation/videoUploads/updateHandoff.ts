import { randomClientId } from "../../clientIdentity";
import type { PendingAttachment } from './contracts';

export interface RefusedUploadWrite { id: string; input: string; method: string; body: BodyInit | null }
interface StoredAttachment { key: string; attachment: PendingAttachment }
const restored = new Map<string, PendingAttachment>();
const known = new Map<string, PendingAttachment>();
const safe = new Map<string, string | true>();
const resumed = new Set<string>();
const owners = new Set<() => void>();
let pending = Promise.resolve();
let failure: unknown;
let loaded = false;
function identity(value: PendingAttachment): string {
  return JSON.stringify([value.destination.kind, value.destination.id, value.destination.expected_conversation_id, value.id, value.fileName]);
}
function session(): string {
  const scope = typeof document === 'undefined' ? 'test' :
    document.querySelector<HTMLMetaElement>('meta[name="panels-server-address"]')?.content || location.origin;
  const key = 'panels.attachment-update-session:' + scope;
  let value = sessionStorage.getItem(key);
  if (!value) { value = randomClientId(); sessionStorage.setItem(key, value); }
  return value;
}
async function storage(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest): Promise<unknown> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('panels-attachment-update-handoff', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('attachments', { keyPath: 'key' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction('attachments', mode);
      const request = action(transaction.objectStore('attachments'));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally { db.close(); }
}
function snapshot(value: PendingAttachment): PendingAttachment {
  return { id: value.id, fileName: value.fileName, byteCount: value.byteCount, mediaType: value.mediaType,
    destination: { ...value.destination }, uploadId: value.uploadId, phase: value.phase,
    receivedBytes: value.receivedBytes, error: value.error, file: value.file, width: value.width, height: value.height };
}
/** Blob bytes stay in IndexedDB, never JSON or session storage. */
export function rememberAttachmentFile(value: PendingAttachment): void {
  if (typeof indexedDB === 'undefined' || typeof sessionStorage === 'undefined') return;
  known.set(identity(value), value);
}
export function forgetAttachmentFile(value: PendingAttachment): void {
  const id = identity(value);
  known.delete(id); restored.delete(id); safe.delete(id); resumed.delete(id);
  if (typeof indexedDB !== 'undefined' && typeof sessionStorage !== 'undefined') {
    const key = session() + ':' + id;
    pending = pending.then(async () => { await storage('readwrite', store => store.delete(key)); }).catch(error => { failure = error; });
  }
}
/** Write at attachment intake and before replacement. Progress never recopies a large Blob. */
export function checkpointAttachmentFiles(): void {
  if (typeof indexedDB === 'undefined' || typeof sessionStorage === 'undefined') return;
  const prefix = session() + ':';
  const values = [...known].map(([key, attachment]) => ({key: prefix + key, attachment: snapshot(attachment)}));
  pending = pending.then(async () => {
    for (const value of values) await storage('readwrite', store => store.put(value));
  }).catch(error => { failure = error; });
}
export async function prepareAttachmentUpdateHandoff(): Promise<void> {
  checkpointAttachmentFiles();
  await pending;
  if (failure) throw failure;
}
export function hydrateAttachmentFile(value: PendingAttachment): PendingAttachment {
  return restored.get(identity(value)) ?? value;
}
export function watchRestoredAttachments(callback: () => void): () => void {
  owners.add(callback);
  return () => owners.delete(callback);
}
export function takeRefusedAttachment(value: PendingAttachment): string | true | undefined {
  const key = identity(value);
  const result = safe.get(key);
  if (result) resumed.add(key);
  safe.delete(key);
  return result;
}
export function wasRefusedAttachmentResumed(value: PendingAttachment): boolean { return resumed.has(identity(value)); }
/** Restore bytes first. Only an explicit pre-handler refusal permits automatic transfer. */
export async function restoreAttachmentUpdateHandoff(refusals: readonly RefusedUploadWrite[]): Promise<void> {
  if (!loaded) {
    const prefix = session() + ':';
    const entries = await storage('readonly', store => store.getAll()) as StoredAttachment[];
    for (const entry of entries) {
      if (!entry.key.startsWith(prefix)) continue;
      const value = entry.attachment;
      value.phase = value.phase === 'ready' ? 'validating' : 'interrupted';
      if (value.file && !(value.file instanceof File)) value.file = new File([value.file], value.fileName, { type: value.mediaType });
      if (value.file) {
        if (/^image\/(png|jpeg|gif|webp)$/.test(value.mediaType)) value.previewUrl = URL.createObjectURL(value.file);
        else if (/^video\//.test(value.mediaType)) value.localVideoUrl = URL.createObjectURL(value.file);
      }
      restored.set(identity(value), value);
      known.set(identity(value), value);
    }
    loaded = true;
  }
  for (const refusal of refusals) {
    for (const [key, value] of restored) {
      if (!value.file) continue;
      if (refusal.method === 'PUT' && value.uploadId && refusal.input === `/api/conversation/video-uploads/${encodeURIComponent(value.uploadId)}/bytes`) {
        safe.set(key, value.uploadId);
      } else if (refusal.method === 'POST' && refusal.input === '/api/conversation/video-uploads' && typeof refusal.body === 'string') {
        const body = JSON.parse(refusal.body);
        if (body.filename === value.fileName && body.byte_count === value.byteCount
          && body.destination?.kind === value.destination.kind && body.destination?.id === value.destination.id
          && body.destination?.expected_conversation_id === value.destination.expected_conversation_id) safe.set(key, true);
      }
    }
  }
  for (const owner of owners) owner();
}
