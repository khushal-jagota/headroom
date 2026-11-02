import { fetchJson } from '../api';
import { queryClient } from '../queryClient';
import { goalQueries } from './queries';
import { updatePageBlock } from './presentation';
import type { Block, GoalEditBody, GoalResponse } from './contracts';

type GoalPatch = Omit<GoalEditBody, 'expected_revision'>;
type EditorIO = {
  read(id: string): Promise<GoalResponse>;
  write(id: string, body: GoalEditBody): Promise<GoalResponse>;
  refresh(): Promise<unknown>;
};

const http: EditorIO = {
  async read(id) { return (await queryClient.fetchQuery(goalQueries.detail(id))).body; },
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

/** Capture identity once, so a late command cannot follow a changed component prop. */
export function createGoalEditor(goalId: string, io: EditorIO = http) {
  async function getAccepted() {
    const goal = await io.read(goalId);
    if (goal.id !== goalId) throw new Error('The accepted goal does not match this edit.');
    return goal;
  }
  async function accept(patch: GoalPatch, revision: number) {
    const accepted = await io.write(goalId, { ...patch, expected_revision: revision });
    // A read failure after acceptance belongs to the mounted query, never Retry save.
    await io.refresh().catch(() => undefined);
    return accepted;
  }
  return {
    goalId,
    getAccepted,
    write: (patch: GoalPatch, revision?: number) => enqueue(goalId, async () => {
      const goal = await getAccepted();
      return accept(patch, revision ?? goal.revision);
    }),
    updateBlock: (blockId: string, update: (block: Block) => Block, revision?: number) => enqueue(goalId, async () => {
      const goal = await getAccepted();
      let found = false;
      const blocks = updatePageBlock(goal.page.blocks, blockId, block => { found = true; return update(block); });
      if (!found) throw new Error('This block no longer exists. Refresh the goal before another edit.');
      return accept({ page: { ...goal.page, blocks } }, revision ?? goal.revision);
    }),
  };
}
