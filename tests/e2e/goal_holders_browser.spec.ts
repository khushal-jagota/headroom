/**
 * Goal promotion and retained URLs use the live Manager conversation.
 *
 * The browser and backend together prove that a retained legacy conversation cannot
 * replace the composer, and that the first message creates the holder conversation.
 */
import type { Request } from '@playwright/test';
import {
  expectResponse,
  WAIT_MS,
  api,
  bindConversation,
  captureSuccess,
  expect as baseExpect,
  openPage,
  startConversation,
  test,
  withDatabase,
} from './support/fixtures.ts';

// The Python tests set every expectation in this module to WAIT_MS.
const expect = baseExpect.configure({ timeout: WAIT_MS });

/** Python's urllib.parse.unquote: decode percent escapes, never throw on a stray one. */
function unquote(url: string): string {
  try {
    return decodeURIComponent(url);
  } catch {
    return url;
  }
}

test('test_goal_promotion_and_stable_url_share_manager_name', async ({ signalServer, newContext }) => {
  const server = signalServer;
  const goal = await api.post(server, '/api/goals', {
    title: 'Launch the guide',
    project_id: 'project_other',
    kind: 'binary',
    primary_checklist_block_id: null,
  });
  const legacyConversation = 'retained-goal-conversation';
  await startConversation(server, legacyConversation);
  await bindConversation(server, 'agent', `goal_${goal.id}`, legacyConversation);
  const goalRoute = `#/workspace/goal/${goal.id}`;
  const context = await newContext();
  const page = await openPage(context, server, goalRoute, `[data-goal-page="${goal.id}"]`);
  await expect(page.getByText('Previous goal conversation', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Make it a Manager', exact: true }).click();
  await page.waitForURL('**/#/workspace/manager/*', { timeout: WAIT_MS });
  const managerId = (await api.get(server, `/api/goals/${goal.id}`)).holder_id;
  const host = page.locator(`[data-manager-view="${managerId}"]`);
  await expect(host.locator('h1')).toContainText(goal.title);
  await expect(page.getByText('Previous goal conversation', { exact: true })).toHaveCount(0);
  let manager = (await api.get(server, `/api/managers/${managerId}/workspace`)).manager;
  expect(manager.conversation_id).toBeNull();

  // The retained Goal URL opens the Manager page, whose name is read-only there. A rename
  // reaches the page and the rail through the Manager API, while the goal title stays hidden.
  await page.goto(`${server.base}/${goalRoute}`);
  const name = 'Launch team';
  await api.patch(server, `/api/managers/${managerId}`, { title: name });
  const railTitle = page.locator(`.manager-card[data-manager="${managerId}"] .item-title`);
  await page.reload();
  await expect(host.locator('h1')).toHaveText(name);
  await expect(railTitle).toHaveText(name);

  await expect(page.getByText('Previous goal conversation', { exact: true })).toHaveCount(0);
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  const composer = page.locator('[data-conversation-input]');
  await expect(composer).toBeEditable({ timeout: WAIT_MS });
  await composer.fill('Help launch this guide', { timeout: WAIT_MS });
  const deliveredResponse = page.waitForResponse(
    (response) => response.url().endsWith(`/api/managers/${managerId}/conversation/send`),
    { timeout: WAIT_MS },
  );
  await composer.press('Enter', { timeout: WAIT_MS });
  const delivered = await deliveredResponse;
  await expectResponse(delivered.ok(), true, delivered);
  const conversationId = (await delivered.json()).conversation_id;
  expect(conversationId).not.toBe(legacyConversation);
  manager = (await api.get(server, `/api/managers/${managerId}/workspace`)).manager;
  expect(manager.conversation_id).toBe(conversationId);
  const wire: string = (await api.get(server, `/api/test/signals/${conversationId}/delivered`)).texts.at(-1);
  expect(wire).toContain('Help launch this guide');
  expect(wire).not.toContain('You are the agent for goal ');
  await api.post(server, `/api/test/signals/${conversationId}/finish`, {});
  await captureSuccess(page, 'goal-holder-first-message.png');

  // Both URLs reopen the same live composer without changing the retained rows.
  await page.goto(`${server.base}/#/workspace/manager/${managerId}`);
  await expect(host).toBeVisible({ timeout: WAIT_MS });
  await expect(page.getByText('Previous goal conversation', { exact: true })).toHaveCount(0);
  await expect(page.locator('[data-conversation-input]')).toBeEditable({ timeout: WAIT_MS });
  await captureSuccess(page, 'manager-holder-conversation.png');
  const [agentRow, conversationRow] = withDatabase(server, (db) =>
    db.read((tx) => [
      tx.get<Record<string, unknown>>('SELECT conversation_id FROM agents WHERE agent_key=?', [`goal_${goal.id}`]),
      tx.get<Record<string, unknown>>('SELECT 1 FROM conversations WHERE conversation_id=?', [legacyConversation]),
    ]),
  );
  expect(agentRow && Object.values(agentRow)).toEqual([legacyConversation]);
  expect(conversationRow && Object.values(conversationRow)).toEqual([1]);
});

test('test_leaving_a_goal_manager_with_text_blocks_opens_each_next_page', async ({ signalServer, newContext }) => {
  // Unmounting the goal's inline text editors must not abort the next page's update.
  const server = signalServer;
  const goal = await api.post(server, '/api/goals', {
    title: 'Write the handbook',
    project_id: 'project_other',
    kind: 'binary',
    primary_checklist_block_id: null,
    page: {
      kind: 'headroom.page',
      version: 1,
      title: 'Approach',
      summary: '',
      blocks: [{ type: 'text', id: 'plan', text: 'Draft every chapter.' }],
    },
  });
  const other = await api.post(server, '/api/managers', { title: 'Other manager', project_id: 'project_other' });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Handbook index',
    worker_type: 'coding',
    brief: 'Agreed brief.',
    manager: { kind: 'manager', id: other.id },
  });
  // Text blocks are editable only on a standalone Goal page, so the goal stays unpromoted.
  const goalRoute = `#/workspace/goal/${goal.id}`;
  const goalPage = `[data-goal-page="${goal.id}"]`;
  const page = await openPage(await newContext(), server, goalRoute, goalPage);
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  // The goal's text block is mounted as an inline editor: the teardown under test.
  const textEditor = page.locator(goalPage).getByRole('textbox', { name: 'Text', exact: true });

  const navigate = async (route: string) => {
    // In-app hash navigation: the old page is torn down by the client, not a reload.
    await page.evaluate((route) => {
      window.location.hash = route;
    }, route);
  };

  const destinations = [
    {
      route: '#/workspace/chief-of-staff',
      requestPath: '/api/chief/page',
      content: page.locator('.chief-workspace-host h1'),
      title: 'Chief of Staff',
    },
    {
      route: `#/workspace/manager/${other.id}`,
      requestPath: `/api/managers/${other.id}/`,
      content: page.locator(`[data-manager-view="${other.id}"] h1`),
      title: other.title as string,
    },
    {
      route: `#/workspace/${ticket.id}`,
      requestPath: `/api/tickets/${ticket.id}/`,
      content: page.locator(`[data-ticket-id="${ticket.id}"]`).getByText(ticket.title).first(),
      title: ticket.title as string,
    },
  ];
  for (const { route, requestPath, content, title } of destinations) {
    await navigate(goalRoute);
    await expect(textEditor).toContainText('Draft every chapter.');

    // Some reads travel as resource-body?key=<path>, so match the decoded URL.
    const ownRead = (request: Request) => unquote(request.url()).includes(requestPath);

    const read = page.waitForRequest(ownRead, { timeout: WAIT_MS });
    await navigate(route);
    await read;
    await expect(content).toContainText(title);
  }
  expect(pageErrors).toEqual([]);
});
