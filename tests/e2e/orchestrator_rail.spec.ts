/**
 * Prove rail writes, invalidation and navigation against an isolated live server.
 *
 * Settle/Reopen can hide outstanding work or replace the open Worker if the HTTP write,
 * board projection, SSE invalidation and route selection disagree. Frontend mocks and
 * backend tests cannot prove those boundaries together. Helper tests own grouping rules;
 * this small browser proof owns the connected behavior and keyboard/pointer access.
 * No backend prompts occur: deterministic durable messages provide unread evidence.
 */
import {
  CHIEF,
  WAIT_MS,
  api,
  bindConversation,
  captureSuccess,
  connectedMessage,
  expect,
  managerPrincipal,
  openPage,
  startConversation,
  test,
  ticketPrincipal,
  type JsonObject,
  type ServerHandle,
} from './support/fixtures.ts';

const LONG_TITLE = 'Review the entire Worker title when the adjustable rail clips this long approval row';

async function seed(server: ServerHandle): Promise<[JsonObject, JsonObject, JsonObject, JsonObject]> {
  const project = await api.post(server, '/api/projects', { name: 'Rail proof' });
  const item = await api.post(server, '/api/managers', {
    title: 'Review the connected retirement rail',
    project_id: project.id,
  });
  const empty = await api.post(server, '/api/managers', {
    title: 'Empty manager stays visible',
    project_id: project.id,
  });
  const ticket = await api.post(server, '/api/tickets', {
    title: LONG_TITLE,
    worker_type: 'coding',
    brief: 'Review the brief.',
    manager: { kind: 'manager', id: item.id },
  });
  // Empty and Blocked children belong in the hover details, not attention groups.
  for (const [title, blockers] of [
    ['Quiet ordinary Worker', []],
    ['Blocked ordinary Worker', [ticket.id]],
  ] as const) {
    const child = await api.post(server, '/api/tickets', {
      title,
      worker_type: 'coding',
      brief: 'Agreed brief.',
      manager: { kind: 'manager', id: item.id },
      blocked_by_ticket_ids: blockers,
    });
    await api.post(server, `/api/tickets/${child.id}/accept/brief`, {
      next_ceiling: 'none',
      next_holder: { kind: 'owner', id: 'owner' },
    });
  }
  const question = await api.post(server, '/api/tickets', {
    title: 'Answer only Worker with no direct message',
    worker_type: 'coding',
    brief: 'Agreed brief.',
    manager: { kind: 'manager', id: item.id },
  });
  await api.post(server, `/api/tickets/${question.id}/accept/brief`, {
    next_ceiling: 'none',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  await startConversation(server, 'rail-question');
  await bindConversation(server, 'ticket', question.id, 'rail-question');
  await api.post(server, '/api/test/signals/rail-question/start', {});
  await api.post(server, '/api/test/signals/rail-question/questions', {});
  await startConversation(server, 'rail-unread');
  await bindConversation(server, 'ticket', ticket.id, 'rail-unread');
  await connectedMessage(server, 'rail-unread', ticketPrincipal(ticket.id), 'Rail direct message');
  for (const conversation of ['rail-chief', 'rail-supervisor']) await startConversation(server, conversation);
  await bindConversation(server, 'agent', item.manager.agent_key, 'rail-supervisor');
  await bindConversation(server, 'agent', 'chief_of_staff', 'rail-chief');
  return [item, empty, ticket, question];
}

/** Python's `datetime.fromtimestamp(seconds, UTC).isoformat()` for whole seconds. */
function utcIsoformat(seconds: number): string {
  return new Date(seconds * 1000).toISOString().replace(/\.\d{3}Z$/, '+00:00');
}

test('test_settle_reopen_keeps_outstanding_work_and_worker_address', async ({ railServer, newContext }) => {
  const server = railServer;
  const [item, empty, ticket, question] = await seed(server);
  const context = await newContext();
  await context.addInitScript(`
      window.__railOrchestratorFields = {};
      const Source = window.EventSource;
      window.EventSource = class extends Source {
        constructor(...args) {
          super(...args);
          for (const type of ['snapshot', 'patch']) this.addEventListener(type, event => {
            const frame = JSON.parse(event.data);
            for (const record of frame.content?.records ?? frame.upserts ?? []) {
              if (record.kind === 'manager')
                window.__railOrchestratorFields[record.id] = Object.keys(record.value).sort();
            }
          });
        }
      };
    `);
  const page = await openPage(context, server, '#/workspace?view=managers', '[data-screen="workspace"]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const left = page.locator('.board-workspace-left');
  const card = left.locator(`[data-manager="${item.id}"]`);
  const row = card.locator(`[data-ticket-id="${ticket.id}"]`);
  await expect(left.locator('[data-chief-destination]')).toBeVisible({ timeout: WAIT_MS });
  await expect(card).toBeVisible({ timeout: WAIT_MS });
  await expect(left.locator('.manager-card').first()).toHaveClass(/\bchief\b/);
  await expect(
    left.locator('.manager-card.chief').getByRole('button', { name: 'Settle', exact: false }),
  ).toHaveCount(0);
  await expect(card.locator('.manager-eyebrow')).toContainText('Rail proof');
  await expect(left.locator(`[data-manager="${empty.id}"]`)).toBeVisible();
  const emptySettled = await api.patch(server, `/api/managers/${empty.id}/settled`, { settled: true });
  // Advance the fixed clock so the later settlement is newer through the server's write door.
  const nextUpdate = utcIsoformat(Math.trunc(Number(emptySettled.updated_at)) + 1);
  await api.post(server, '/api/test/set-now', { now: nextUpdate });
  // The unread message takes the mark column ahead of the pending approval.
  // It stays through settle, reopen, and reads of the other conversations below.
  // The approval bucket independently preserves the pending proposal.
  const attention = row.getByRole('img', { name: 'Unread message' });
  await expect(attention).toBeVisible({ timeout: WAIT_MS });
  await expect(card.locator('.item-head .stage-mark')).toHaveCount(0);
  await expect(left.locator('[data-chief-destination] .stage-mark')).toHaveCount(0);
  const questionRow = card.locator(`[data-ticket-id="${question.id}"]`);
  await expect(questionRow).toBeVisible({ timeout: WAIT_MS });
  await expect(questionRow).toContainText('Answer only Worker with no direct message');
  const workingMark = questionRow.getByRole('img', { name: 'Working', exact: true });
  await expect(workingMark).toHaveCount(1);
  await expect(workingMark).toBeVisible();
  await expect(card.locator('[data-bucket-key="awaiting_approval"]')).toContainText(LONG_TITLE);
  await row.click();
  const workerHash = await page.evaluate(() => location.hash);
  await expect(page.locator(`[data-screen="ticket"][data-ticket-id="${ticket.id}"]`)).toBeVisible();
  // Focus keeps Settle interactive even if the header moves before the click.
  const settleButton = card.getByRole('button', { name: `Settle ${item.title}`, exact: true });
  await settleButton.focus();
  await expect(settleButton).toBeFocused();
  const writePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/managers/${item.id}/settled`) && response.request().method() === 'PATCH',
  );
  await settleButton.click();
  const write = await writePromise;
  expect(write.ok()).toBe(true);
  // Keep this membership proof outside the intentional Worker-hover freeze.
  await page.mouse.move(1000, 50);
  await expect(card).toHaveCount(0, { timeout: WAIT_MS });
  expect((await write.json()).settled).toBe(true);
  expect(await page.evaluate(() => location.hash)).toBe(workerHash);
  await expect(page.locator(`[data-screen="ticket"][data-ticket-id="${ticket.id}"]`)).toBeVisible();
  // Settlement changes arrangement only. Existing global views retain attention.
  await page.goto(server.base + '/#/review');
  await expect(page.locator(`[data-review-card][data-ticket-id="${ticket.id}"]`)).toBeVisible({
    timeout: WAIT_MS,
  });
  // The retired global list redirects to Workspace. Settled parents retain child access.
  await page.goto(server.base + '/#/backlog?project=' + item.project_id);
  await page.waitForFunction("location.hash === '#/workspace'", undefined, { timeout: WAIT_MS });
  await expect(page.locator('[data-backlog-tickets]')).toHaveCount(0);
  await left.getByRole('button', { name: 'Settled', exact: false }).click();
  const leanTitle = left.locator(`[data-settled-item="${empty.id}"] .settled-title`);
  await expect(leanTitle).toBeVisible({ timeout: WAIT_MS });
  await page.waitForFunction(
    (id) =>
      JSON.stringify(
        (window as unknown as { __railOrchestratorFields: Record<string, string[]> }).__railOrchestratorFields[id],
      ) === JSON.stringify(['conversation_id', 'created_at', 'settled', 'title', 'updated_at']),
    empty.id as string,
    { timeout: WAIT_MS },
  );
  await leanTitle.hover();
  await leanTitle.focus();
  await page.evaluate(
    () => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))),
  );
  await expect(page.locator('[data-rail-hover-panel]')).toHaveAttribute('aria-hidden', 'true');
  await captureSuccess(page, 'settled-lean-no-popover.png');
  const scopedParent = left.locator(`[data-settled-item="${item.id}"]`);
  await expect(scopedParent).toBeVisible({ timeout: WAIT_MS });
  await expect(left.locator('[data-settled-item] .settled-title > span')).toHaveText([item.title, empty.title]);
  await expect(left.locator('[data-settled-scroll] [data-message-conversation]')).toHaveCount(0);
  await expect(left.locator('[data-settled-scroll] [data-workspace-messages]')).toHaveCount(0);
  await expect(scopedParent.getByRole('button', { name: `Reopen ${item.title}` })).toBeVisible();
  await captureSuccess(page, 'settled-managers-only.png');
  await scopedParent.locator('.settled-title').click();
  const scopedPane = page.locator(`[data-manager-workspace="${item.id}"]`);
  const scopedRow = scopedPane.locator(`[data-worker-ticket-id="${ticket.id}"]`);
  // An absent row can mean loading, so inspect the mounted fold before toggling it.
  const scopedGroup = scopedPane.locator('[data-workspace-group="awaiting_approval"]');
  await expect(scopedGroup).toBeVisible({ timeout: WAIT_MS });
  if ((await scopedGroup.getAttribute('open')) === null) await scopedGroup.locator(':scope > summary').click();
  await expect(scopedRow).toBeVisible({ timeout: WAIT_MS });
  await expect(scopedRow).toContainText(LONG_TITLE);
  await scopedRow.click();
  await expect(page.locator(`[data-screen="ticket"][data-ticket-id="${ticket.id}"]`)).toBeVisible();
  await page.goBack();
  await expect(scopedRow).toBeVisible({ timeout: WAIT_MS });
  await page.goto(server.base + '/' + workerHash);
  await page.reload();
  await expect(page.locator(`[data-screen="ticket"][data-ticket-id="${ticket.id}"]`)).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe(workerHash);
  await left.getByRole('button', { name: 'Settled', exact: false }).click();
  const settled = left.locator(`[data-settled-item="${item.id}"]`);
  await expect(settled).toBeVisible();
  await settled.getByRole('button', { name: 'Reopen', exact: false }).click();
  await expect(card).toBeVisible({ timeout: WAIT_MS });
  // Python read managers.settled from SQLite; the Manager read carries the same column.
  expect((await api.get(server, `/api/managers?detail=full&id=${item.id}`)).settled).toBe(false);
  await expect(attention).toBeVisible();
  await captureSuccess(page, 'rail-1440.png');
  await connectedMessage(server, 'rail-chief', CHIEF, 'Chief own direct message');
  await connectedMessage(server, 'rail-supervisor', managerPrincipal(item.id), 'Supervisor own direct message');
  await page.reload();
  await expect(card.locator('.item-head .stage-mark')).toBeVisible({ timeout: WAIT_MS });
  await expect(left.locator('[data-chief-destination] .stage-mark')).toBeVisible({ timeout: WAIT_MS });
  await expect(attention).toBeVisible();
  await api.post(server, '/api/conversation/conversations/rail-chief/owner-read', { through_sequence: 1 });
  await expect(left.locator('[data-chief-destination] .stage-mark')).toHaveCount(0, { timeout: WAIT_MS });
  await expect(card.locator('.item-head .stage-mark')).toBeVisible();
  await expect(attention).toBeVisible();
  await captureSuccess(page, 'own-conversation-marks.png');
});
