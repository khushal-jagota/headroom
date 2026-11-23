import { mutateJson, screenShowsOwnWrite } from '../mutate';
import { tick } from 'svelte';
import { queryClient } from '../queryClient';
import { updatePageBlock } from './presentation';
import { goalQueries } from './queries';
import type { Block, GoalEditBody, GoalResponse, PageDocument } from './contracts';

type GoalPatch = GoalEditBody;
type EditorIO = {
  read(id: string): Promise<GoalResponse>;
  write(id: string, body: GoalEditBody): Promise<GoalResponse>;
  /** Resolves when the screen shows the accepted write, or at a short limit. */
  refresh(): Promise<unknown>;
};

const http: EditorIO = {
  read: async id => (await queryClient.fetchQuery(goalQueries.detail(id))).body,
  write: (id, body) => mutateJson<GoalResponse>(`/api/goals/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  refresh: screenShowsOwnWrite,
};

// Only unfinished commands live here. Accepted documents remain in server queries.
const tails = new Map<string, Promise<unknown>>();
const writingListeners = new Map<string, Set<(writing: boolean) => void>>();
function publishWriting(id: string) {
  for (const listener of writingListeners.get(id) ?? []) listener(tails.has(id));
}
/**
 * The write ends when the server answers, and a queued edit starts then. The caller and
 * the writing mark wait a little longer, until the screen shows the accepted document or
 * a short limit passes. A control drops its own pending state when its edit resolves, so
 * without that wait it shows the old state for a moment.
 */
function enqueue<T>(id: string, command: () => Promise<T>, shown: () => Promise<unknown>): Promise<T> {
  const next = (tails.get(id) ?? Promise.resolve()).catch(() => undefined).then(command);
  tails.set(id, next);
  publishWriting(id);
  const visible = next.then(async accepted => { await shown().catch(() => undefined); return accepted; });
  void visible.catch(() => undefined).finally(async () => {
    // Query observers update synchronously; let their accepted props reach controls.
    await tick();
    if (tails.get(id) === next) { tails.delete(id); publishWriting(id); }
  });
  return visible;
}

/**
 * The revisions that this browser's own accepted writes moved each document through.
 *
 * The server admits a write only at the current revision. So an accepted write from one
 * revision to the next proves that nothing else changed the document between them. A
 * control that still shows a revision from this chain differs from the newest one only
 * by this browser's own edits, and its edit is sent at the newest. The editor then needs
 * no read before the next edit. A change from elsewhere is still refused: it is not in
 * the chain, so the server sees an old revision.
 */
const ownRevisions = new Map<string, { passed: Set<number>; newest: number }>();
function admittedRevision(id: string, asked: number): number {
  const own = ownRevisions.get(id);
  return own?.passed.has(asked) ? own.newest : asked;
}
function isSuperseded(id: string, revision: number): boolean {
  return ownRevisions.get(id)?.passed.has(revision) === true;
}
function recordAccepted(id: string, from: number, to: number): void {
  // A revision that did not move forward is not this chain. Start again from nothing.
  if (to <= from) { ownRevisions.delete(id); return; }
  const own = ownRevisions.get(id);
  if (own && own.newest === from) { own.passed.add(from); own.newest = to; }
  else ownRevisions.set(id, { passed: new Set([from]), newest: to });
}

export function createPageEditor<Document, Patch extends {expected_revision?: number}>(id: string, io: {
  read(): Promise<Document>;
  write(patch: Patch): Promise<Document>;
  page(document: Document): PageDocument | null;
  pagePatch(page: PageDocument, revision: number): Patch;
  revision(document: Document): number;
  refresh(): Promise<unknown>;
}) {
  async function accept(patch: Patch) {
    const from = patch.expected_revision;
    let accepted: Document;
    try {
      accepted = await io.write(patch);
    } catch (error) {
      // A refusal means that the document moved elsewhere. No answer means that this
      // write can be saved or not. In both cases the chain says nothing true any more.
      ownRevisions.delete(id);
      throw error;
    }
    if (from !== undefined) recordAccepted(id, from, io.revision(accepted));
    return accepted;
  }
  return {
    getAccepted: io.read,
    subscribeWriting(listener: (writing: boolean) => void) {
      const listeners = writingListeners.get(id) ?? new Set<(writing: boolean) => void>();
      writingListeners.set(id, listeners);
      listeners.add(listener);
      listener(tails.has(id));
      return () => {
        listeners.delete(listener);
        if (!listeners.size) writingListeners.delete(id);
      };
    },
    // A patch names fields. It does not depend on the rest of the document, so a control
    // that still shows this browser's own earlier edit is admitted at the newest revision.
    write: (patch: Patch) => enqueue(id, async () => accept({
      ...patch, expected_revision: admittedRevision(id, patch.expected_revision ?? io.revision(await io.read())),
    }), io.refresh),
    updateBlock: (blockId: string, update: (block: Block) => Block, expectedRevision?: number) => enqueue(id, async () => {
      // The whole page is sent, so it must be built on a document that holds this
      // browser's own earlier edits and everything that the control showed. A read that
      // began before those does not, so it is read once more.
      const behind = (read: Document) => isSuperseded(id, io.revision(read)) ||
        (expectedRevision !== undefined && io.revision(read) < admittedRevision(id, expectedRevision));
      let document = await io.read();
      if (behind(document)) document = await io.read();
      if (behind(document)) {
        ownRevisions.delete(id);
        throw new Error('This page is not up to date yet. Try the edit again.');
      }
      const basis = io.revision(document), page = io.page(document);
      if (!page) throw new Error('This page no longer exists.');
      let found = false;
      const blocks = updatePageBlock(page.blocks, blockId, block => { found = true; return update(block); });
      if (!found) throw new Error('This block no longer exists. Refresh the page before another edit.');
      const asked = expectedRevision ?? basis;
      return accept(io.pagePatch({ ...page, blocks }, admittedRevision(id, asked) === basis ? basis : asked));
    }, io.refresh),
  };
}

/** Capture identity once, so a late command cannot follow a changed component prop. */
export function createGoalEditor(goalId: string, io: EditorIO = http) {
  return {goalId, ...createPageEditor<GoalResponse, GoalPatch>(goalId, {
    async read() {
      const goal = await io.read(goalId);
      if (goal.id !== goalId) throw new Error('The accepted goal does not match this edit.');
      return goal;
    },
    write: patch => io.write(goalId, patch),
    page: goal => goal.page,
    pagePatch: (page, expected_revision) => ({page, expected_revision}),
    revision: goal => goal.revision,
    refresh: io.refresh,
  })};
}
