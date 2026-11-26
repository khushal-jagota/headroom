import { writable } from 'svelte/store';
import { clientIdentityHeaders, randomClientId } from '../clientIdentity';
import { signalWithLimit } from '../requestLimit/limit';
import { REQUEST_LIMIT_MS } from '../requestLimit/config';
import { noteUpdateRefusal, updateRefusal } from './writePause';
import { refreshScreen } from '../resourceVersions/refresh';

export interface RecoveredDraft { id: string; label: string; value: string }
export const recoveredDrafts = writable<RecoveredDraft[]>([]);
interface SavedField extends RecoveredDraft { route: string; selector: string; checked?: boolean }
interface SavedWrite {
  id: string; input: string; method: string; headers: [string, string][];
  order: number; body: BodyInit | null; form?: [string, FormDataEntryValue][];
}
function serverScope(): string {
  return document.querySelector<HTMLMetaElement>('meta[name="panels-server-address"]')?.content || location.origin;
}
function fieldsKey(): string { return 'panels.update-fields:' + serverScope(); }
function recoveryKey(): string { return 'panels.update-recovery:' + serverScope(); }
const SESSION_KEY = 'panels.update-session';
const live = new Set<string>();
let writes = Promise.resolve();
let recovering: Promise<void> | null = null;
let capture: (() => void) | null = null;
let session: string | null = null;
let nextOrder = Date.now();
let replacing = false;
export function isUpdateScreenReplacement(): boolean { return replacing; }
export function cancelUpdateScreenReplacement(): void {
  replacing = false;
  void import('../conversation/videoUploads/localSends').then(module => module.cancelLocalSendsUpdateHandoff());
}
function sessionId(): string {
  if (session) return session;
  session = sessionStorage.getItem(SESSION_KEY + ':' + serverScope()) || randomClientId();
  sessionStorage.setItem(SESSION_KEY + ':' + serverScope(), session);
  return session;
}
async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('panels-update-handoff', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('writes', { keyPath: 'id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
async function stored<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await database();
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = db.transaction('writes', mode);
      const request = action(transaction.objectStore('writes'));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally { db.close(); }
}
/** Call only for a request that did not leave the page or a proven pre-admission refusal. */
export async function retainRefusedWrite(input: string, init: RequestInit): Promise<string> {
  const id = `${sessionId()}:${randomClientId()}`;
  const entry: SavedWrite = {
    id, input, order: ++nextOrder, method: init.method ?? 'GET', headers: [...new Headers(init.headers).entries()],
    body: init.body instanceof FormData ? null : init.body ?? null,
    ...(init.body instanceof FormData ? { form: [...init.body.entries()] } : {})
  };
  live.add(id);
  const save = stored('readwrite', store => store.put(entry)).then(() => {});
  writes = writes.then(() => save);
  try { await save; } catch (error) { live.delete(id); throw error; }
  return id;
}
/** Remove before dispatch: a lost reply must never leave an automatically replayable request. */
export async function settleRefusedWrite(id: string): Promise<void> {
  await stored('readwrite', store => store.delete(id));
  live.delete(id);
}
function addRecovery(value: RecoveredDraft): void {
  recoveredDrafts.update(values => {
    const next = [...values.filter(item => item.id !== value.id), value];
    sessionStorage.setItem(recoveryKey(), JSON.stringify(next));
    return next;
  });
}
export function dismissRecoveredDraft(id: string): void {
  const fields = readFields().filter(field => field.id !== id);
  sessionStorage.setItem(fieldsKey(), JSON.stringify(fields));
  recoveredDrafts.update(values => {
    const next = values.filter(value => value.id !== id);
    sessionStorage.setItem(recoveryKey(), JSON.stringify(next));
    return next;
  });
}
/** New screens own recovery. Existing callers keep their live promises and resume themselves. */
export function recoverUpdateWrites(appSha: string): Promise<void> {
  if (replacing) return Promise.resolve();
  if (recovering) return recovering;
  recovering = (async () => {
    const entries = await stored('readonly', store => store.getAll()) as SavedWrite[];
    const pending = entries.filter(entry => entry.id.startsWith(sessionId() + ':') && !live.has(entry.id));
    const uploads = pending.filter(entry => entry.input.startsWith('/api/conversation/video-uploads'));
    // The upload owner needs response IDs and the original File. It owns continuation.
    for (const entry of uploads) await settleRefusedWrite(entry.id);
    const { restoreAttachmentUpdateHandoff } = await import('../conversation/videoUploads/updateHandoff');
    await restoreAttachmentUpdateHandoff(uploads);
    for (const entry of pending.filter(entry => !uploads.includes(entry)).sort((a, b) => a.order - b.order)) {
      if (!entry.id.startsWith(sessionId() + ':') || live.has(entry.id)) continue;
      await settleRefusedWrite(entry.id);
      const headers = new Headers(entry.headers);
      headers.set('X-Panels-App-SHA', appSha);
      const body = entry.form ? new FormData() : entry.body;
      if (entry.form && body instanceof FormData) for (const [key, value] of entry.form) body.append(key, value);
      if (entry.form) headers.delete('content-type');
      try {
        const response = await fetch(entry.input, { method: entry.method, body,
          headers: clientIdentityHeaders(headers), signal: signalWithLimit(REQUEST_LIMIT_MS) });
        if (response.ok) {
          window.dispatchEvent(new Event('panels:update-write-recovered'));
          void refreshScreen().catch(() => undefined);
          continue;
        }
        // A second explicit pre-handler refusal is still safe to retain for the next status read.
        const refusal = await updateRefusal(response);
        if (refusal) {
          await stored('readwrite', store => store.put(entry));
          noteUpdateRefusal(refusal);
          break;
        }
        addRecovery({ id: entry.id, label: 'Review your action', value: 'Panels could not finish this action. Check the result before another attempt.\n' + describe(entry) });
      } catch {
        addRecovery({ id: entry.id, label: 'Check your action', value: 'The server did not answer. Check the result before another attempt.\n' + describe(entry) });
      }
    }
  })().finally(() => { recovering = null; });
  return recovering;
}
function describe(entry: SavedWrite): string {
  return entry.form ? entry.form.map(([name, value]) => `${name}: ${typeof value === 'string' ? value : value.name}`).join('\n')
    : typeof entry.body === 'string' ? entry.body : entry.input;
}
function readFields(): SavedField[] {
  try { return JSON.parse(sessionStorage.getItem(fieldsKey()) ?? '[]') as SavedField[]; } catch { return []; }
}
function locator(element: HTMLElement): string {
  if (element.id) return '#' + CSS.escape(element.id);
  const segments: string[] = [];
  let current: HTMLElement | null = element;
  while (current && current.id !== 'app' && current !== document.body) {
    const parent: HTMLElement | null = current.parentElement;
    if (!parent) break;
    segments.unshift(`${current.tagName.toLowerCase()}:nth-child(${[...parent.children].indexOf(current) + 1})`);
    current = parent;
  }
  return `${current?.id === 'app' ? '#app' : 'body'} > ${segments.join(' > ')}`;
}
/** Keep a readable copy through replacement. Never synthesize input/change: some fields save on change. */
export function startUpdateHandoff(): () => void {
  const edits = new Map<HTMLElement, { id: string; route: string; selector: string; label: string }>();
  const retained = readFields();
  try { for (const value of JSON.parse(sessionStorage.getItem(recoveryKey()) ?? '[]') as RecoveredDraft[]) addRecovery(value); } catch { /* Existing entered text remains in its original storage. */ }
  for (const field of retained) addRecovery(field);
  const snapshot = (): void => {
    const fields = new Map(readFields().map(field => [field.id, field]));
    for (const [element, identity] of edits) {
      if (!element.isConnected) continue;
      const value = 'value' in element ? String(element.value) : element.innerText;
      if (!value) continue;
      const checked = element instanceof HTMLInputElement && ['checkbox', 'radio'].includes(element.type)
        ? element.checked : undefined;
      fields.set(identity.id, { ...identity, value: checked === undefined ? value : `${checked ? 'Selected' : 'Not selected'}: ${value}` });
    }
    sessionStorage.setItem(fieldsKey(), JSON.stringify([...fields.values()]));
  };
  const save = (event: Event): void => {
    const element = event.target;
    if (!(element instanceof HTMLElement) || element.closest('[data-update-recovered-drafts], [data-composer-field], [data-credential-request], .private-input')) return;
    if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement || element.isContentEditable)) return;
    if (element instanceof HTMLInputElement && ['password', 'file', 'hidden'].includes(element.type)) return;
    const selector = locator(element);
    edits.set(element, { id: `${location.hash}:${selector}`, route: location.hash, selector,
      label: element.getAttribute('aria-label') || element.getAttribute('placeholder') || 'Your recent edit' });
  };
  document.addEventListener('input', save, true);
  document.addEventListener('change', save, true);
  capture = snapshot;
  window.__panelsCancelScreenReplacement = cancelUpdateScreenReplacement;
  window.__panelsPrepareScreenReplacement = async () => {
    try { await prepareUpdateScreenReplacement(); return true; } catch { return false; }
  };
  return () => { document.removeEventListener('input', save, true); document.removeEventListener('change', save, true); capture = null; delete window.__panelsPrepareScreenReplacement; delete window.__panelsCancelScreenReplacement; };
}
export async function prepareUpdateScreenReplacement(): Promise<void> {
  replacing = true;
  noteUpdateRefusal('screens_outdated');
  try {
    const { prepareLocalSendsUpdateHandoff } = await import('../conversation/videoUploads/localSends');
    prepareLocalSendsUpdateHandoff();
    capture?.();
    const { prepareAttachmentUpdateHandoff } = await import('../conversation/videoUploads/updateHandoff');
    await prepareAttachmentUpdateHandoff();
    await writes;
  } catch (error) { cancelUpdateScreenReplacement(); throw error; }
}

/** Restore files before their composer mounts; admission reconciliation runs later. */
export async function restoreUpdateHandoff(): Promise<void> {
  const { restoreAttachmentUpdateHandoff } = await import('../conversation/videoUploads/updateHandoff');
  await restoreAttachmentUpdateHandoff([]);
}
declare global { interface Window { __panelsCancelScreenReplacement?: () => void; __panelsPrepareScreenReplacement?: () => Promise<boolean> } }
