/**
 * Prove that a Ticket list arrives settled when the reader switches pages.
 *
 * The Tickets section on a Manager or Chief page can draw from rows another surface
 * loaded, and then from count-only windows, before its own rows arrive. Each draw adds
 * or moves groups. That order depends on the live navigation stream, so frontend
 * fixtures cannot prove it. This proof records every draw while the sidebar switches
 * pages, and requires the first drawn list to be the final one.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import type { Page } from '@playwright/test';
import {
  OWNER,
  WAIT_MS,
  api,
  expect,
  openPage,
  proposeAsWorker,
  test,
  type JsonObject,
  type ServerHandle,
} from './support/fixtures.ts';

type GroupDraw = [key: string | undefined, count: string | null | undefined, rows: number];

interface Draw {
  loading: boolean;
  groups: GroupDraw[];
  empty: string | null;
  backlogged: string | null;
  done: string | null;
}

interface DrawWindow {
  __ticketListDraws: Draw[];
  __quiet: { count: number; since: number };
}

// Record each distinct state of the Tickets section, from first mount onward.
function recordDraws(scope: string): void {
  const draws: Draw[] = [];
  let last = '';
  const section = (root: Element, filter: string) => root.querySelector(`[data-worker-section="${filter}"]`);
  const snapshot = () => {
    const root = document.querySelector(`[data-manager-children="${scope}"]`);
    if (!root) return null;
    const active = section(root, 'active');
    const count = (filter: string) =>
      section(root, filter)?.querySelector('[data-group-count]')?.textContent ?? null;
    return JSON.stringify({
      loading: active?.querySelector('[aria-busy="true"]') !== null,
      groups: [...(active?.querySelectorAll<HTMLDetailsElement>('details[data-workspace-group]') ?? [])].map(
        (group) => [
          group.dataset.workspaceGroup,
          group.querySelector('[data-group-count]')?.textContent,
          group.querySelectorAll('[data-worker-ticket-id]').length,
        ],
      ),
      empty: active?.querySelector('.manager-workspace-empty')?.textContent ?? null,
      backlogged: count('backlogged'),
      done: count('done'),
    });
  };
  const observe = () => {
    const next = snapshot();
    if (next && next !== last) {
      draws.push(JSON.parse(next));
      last = next;
    }
  };
  new MutationObserver(observe).observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
  });
  const target = window as unknown as DrawWindow;
  target.__ticketListDraws = draws;
  target.__quiet = { count: -1, since: 0 };
}

async function ticket(
  server: ServerHandle,
  manager: JsonObject,
  title: string,
  extra: JsonObject = {},
): Promise<JsonObject> {
  return api.post(server, '/api/tickets', {
    title,
    worker_type: 'coding',
    brief: 'Agreed brief.',
    manager,
    ...extra,
  });
}

async function finish(server: ServerHandle, created: JsonObject): Promise<void> {
  const identity = String(created.id);
  await api.post(server, `/api/tickets/${identity}/accept/brief`, {
    next_ceiling: 'needs_consequences',
    next_holder: OWNER,
  });
  // Earlier proposals settle below the ceiling. The final report needs acceptance.
  for (const field of ['what_changes', 'implementation', 'consequences'])
    await proposeAsWorker(server, identity, `Synthetic ${field}.`);
  await api.post(server, `/api/tickets/${identity}/accept/consequences`, {});
  expect((await api.get(server, `/api/tickets?detail=full&id=${identity}`)).stage).toBe('done');
}

async function seed(server: ServerHandle): Promise<[JsonObject, JsonObject]> {
  const project = await api.post(server, '/api/projects', { name: 'First draw proof' });
  const first = await api.post(server, '/api/managers', { title: 'Small manager', project_id: project.id });
  const full = await api.post(server, '/api/managers', { title: 'Full manager', project_id: project.id });
  const small = { kind: 'manager', id: first.id };
  const many = { kind: 'manager', id: full.id };
  await ticket(server, small, 'Only Worker on the small manager');
  const blocker = await ticket(server, many, 'Worker that blocks the others');
  for (let index = 0; index < 3; index++) {
    const blocked = await ticket(server, many, `Blocked Worker ${index}`, {
      blocked_by_ticket_ids: [blocker.id],
    });
    await api.post(server, `/api/tickets/${blocked.id}/accept/brief`, {
      next_ceiling: 'none',
      next_holder: OWNER,
    });
  }
  const approval = await ticket(server, many, 'Worker that needs approval');
  await api.post(server, `/api/tickets/${approval.id}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: OWNER,
  });
  await proposeAsWorker(server, String(approval.id), 'Synthetic what changes.');
  const deferred = await ticket(server, many, 'Backlogged Worker');
  await api.post(server, `/api/tickets/${deferred.id}/backlog`, {});
  for (let index = 0; index < 2; index++)
    await finish(server, await ticket(server, many, `Finished Worker ${index}`));
  const chief = { kind: 'chief', id: 'chief' };
  for (let index = 0; index < 3; index++) await ticket(server, chief, `Chief Worker ${index}`);
  return [first, full];
}

async function switchPage(page: Page, scope: string, click: () => Promise<void>): Promise<Draw[]> {
  await page.evaluate(recordDraws, scope);
  await click();
  // Settled means no new draw for a full second after the list is on screen.
  await page.waitForFunction(
    () => {
      const target = window as unknown as DrawWindow;
      const draws = target.__ticketListDraws,
        last = draws.at(-1),
        quiet = target.__quiet;
      if (!last || last.loading || last.done === '') return false;
      if (quiet.count !== draws.length) {
        quiet.count = draws.length;
        quiet.since = performance.now();
      }
      return performance.now() - quiet.since > 1000;
    },
    undefined,
    { polling: 100, timeout: WAIT_MS },
  );
  const draws = await page.evaluate(() => (window as unknown as DrawWindow).__ticketListDraws);
  const evidence = process.env.PANELS_UI_PROOF_DIR;
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    const name = scope.split(':')[0];
    writeFileSync(join(evidence, `ticket-list-draws-${name}.json`), JSON.stringify(draws, null, 1));
    await page.screenshot({ path: join(evidence, `ticket-list-${name}.png`) });
  }
  return draws;
}

function assertSettledOnArrival(draws: Draw[], label: string): void {
  const drawn = draws.filter((draw) => !draw.loading);
  expect(drawn.length, JSON.stringify([label, draws])).toBeGreaterThan(0);
  const final = drawn.at(-1)!;
  // Before the list arrives, history counts are blank or already final.
  for (const draw of draws.slice(0, draws.indexOf(drawn[0]))) {
    expect(['', final.backlogged], JSON.stringify([label, draws])).toContain(draw.backlogged);
    expect(['', final.done], JSON.stringify([label, draws])).toContain(draw.done);
  }
  for (const key of ['groups', 'empty', 'backlogged', 'done'] as const)
    expect(
      drawn.every((draw) => isDeepStrictEqual(draw[key], final[key])),
      JSON.stringify([label, key, draws]),
    ).toBe(true);
}

test('test_ticket_lists_arrive_settled_when_switching_pages', async ({ railServer, newContext }) => {
  const server = railServer;
  const [first, full] = await seed(server);
  const page = await openPage(
    await newContext(),
    server,
    `#/workspace/item/${first.id}`,
    `[data-manager-children="manager:${first.id}"]`,
  );
  page.setDefaultTimeout(WAIT_MS);
  const rail = page.locator('.active-work-scroll');

  let draws = await switchPage(page, `manager:${full.id}`, () =>
    rail.locator(`.manager-card[data-manager="${full.id}"] .item-head`).click(),
  );
  assertSettledOnArrival(draws, 'manager');
  const final = draws.at(-1)!;
  const groups = Object.fromEntries(final.groups.map(([key, count, rows]) => [key, [count, rows]]));
  expect(groups.blocked, JSON.stringify(final)).toEqual(['3', 3]);
  expect(groups.awaiting_approval, JSON.stringify(final)).toEqual(['2', 2]);
  expect([final.backlogged, final.done], JSON.stringify(final)).toEqual(['1', '2']);

  draws = await switchPage(page, 'chief:chief', () => rail.locator('[data-chief-destination]').click());
  assertSettledOnArrival(draws, 'chief');
  expect(
    draws.at(-1)!.groups.reduce((sum, [, , rows]) => sum + rows, 0),
    JSON.stringify(draws.at(-1)),
  ).toBe(3);
});
