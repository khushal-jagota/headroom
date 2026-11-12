import { fetchJson } from '../api';
import { queryClient } from '../queryClient';
import { updatePageBlock } from './presentation';
import { goalQueries } from './queries';
import type { Block, GoalEditBody, GoalResponse, PageDocument } from './contracts';

type GoalPatch = GoalEditBody;
type EditorIO = {
  read(id: string): Promise<GoalResponse>;
  write(id: string, body: GoalEditBody): Promise<GoalResponse>;
  refresh(): Promise<unknown>;
};

const http: EditorIO = {
  read: async id => (await queryClient.fetchQuery(goalQueries.detail(id))).body,
  write: (id, body) => fetchJson<GoalResponse>(`/api/goals/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  refresh: () => queryClient.invalidateQueries(),
};

// Only unfinished commands live here. Accepted documents remain in server queries.
const tails = new Map<string, Promise<unknown>>();
function enqueue<T>(id: string, command: () => Promise<T>): Promise<T> {
  const next = (tails.get(id) ?? Promise.resolve()).catch(() => undefined).then(command);
  tails.set(id, next);
  void next.finally(() => { if (tails.get(id) === next) tails.delete(id); }).catch(() => undefined);
  return next;
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
    const accepted = await io.write(patch);
    // A read failure after acceptance belongs to the mounted query, never Retry save.
    await io.refresh().catch(() => undefined);
    return accepted;
  }
  return {
    getAccepted: io.read,
    write: (patch: Patch) => enqueue(id, async () => accept({
      ...patch, expected_revision: patch.expected_revision ?? io.revision(await io.read()),
    })),
    updateBlock: (blockId: string, update: (block: Block) => Block, expectedRevision?: number) => enqueue(id, async () => {
      const document = await io.read(), page = io.page(document);
      if (!page) throw new Error('This page no longer exists.');
      let found = false;
      const blocks = updatePageBlock(page.blocks, blockId, block => { found = true; return update(block); });
      if (!found) throw new Error('This block no longer exists. Refresh the page before another edit.');
      return accept(io.pagePatch({ ...page, blocks }, expectedRevision ?? io.revision(document)));
    }),
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
