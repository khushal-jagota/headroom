/**
 * Ticket browser adapters through the real core and controlled backend.
 *
 * A mocked pane cannot prove that browser writes reach canonical queue/answer
 * writers, or that a mounted Ticket pane reflects the canonical result.
 * Only the backend wire is scripted. No installed provider participates.
 */
import { randomUUID } from 'node:crypto';
import {
  expectResponse,
  WAIT_MS,
  agentHeaders,
  captureSuccess,
  cli,
  expect,
  test,
  withDatabase,
  type JsonObject,
} from './support/fixtures.ts';
import {
  createTicketConversation,
  getJson,
  openConversationLayer,
  openTicketConversation,
  post,
} from './support/conversation.ts';
import { read_encrypted, read_metadata } from '../../server/credentials/data.ts';

test('test_agent_questions_survive_reload_submit_as_one_map_and_replay_answers', async ({
  signalServer,
  newContext,
}) => {
  // Browser answers reach the backend sink and survive durable replay.
  const server = signalServer;
  const conversationId = 'e2e-ticket-agent-questions';
  const ticketId = await createTicketConversation(server, {
    conversationId,
    title: 'Answer agent questions',
  });
  await post(server, `/api/test/signals/${conversationId}/questions`);
  const context = await newContext();
  const page = await openTicketConversation(server, ticketId, context, '[data-user-input-panel]');
  expect(await page.locator('[data-user-input-panel]').innerText()).not.toContain('Approve');
  await page.reload();
  await page.waitForSelector('[data-conversation-rest-bar]', { timeout: WAIT_MS });
  // The waiting question shows in the dock on first load, before any open and close.
  await expect(page.locator('[data-conversation-ask-dock] [data-user-input-panel]')).toBeVisible({ timeout: WAIT_MS });
  await openConversationLayer(page);
  await page.waitForSelector('[data-user-input-panel]', { timeout: WAIT_MS });
  await page.locator('[data-conversation-lens-choice="full"]').click();
  await page.locator('[data-user-input-option="Composer"]').click();
  await page.locator('[data-user-input-option="Transcript"]').click();
  // The second question allows its own answer: written in the composer's reply line, sent with Send.
  await page.locator('[data-conversation-send]').click();
  await page.locator('[data-conversation-input]').fill('Recorded event replay');
  const submittedRequest = page.waitForRequest(
    (request) => request.url().endsWith('/user-input-answers') && request.method() === 'POST',
  );
  const answeredResponse = page.waitForResponse(
    (response) => response.url().endsWith('/user-input-answers') && response.request().method() === 'POST',
  );
  await page.locator('[data-conversation-send]').click();
  const answered = await answeredResponse;
  const submitted = await submittedRequest;
  const expectedAnswers = {
    scope: { answers: ['Composer', 'Transcript'] },
    proof: { answers: ['Recorded event replay'] },
  };
  expect(submitted.postDataJSON()).toEqual({
    request_id: '1:user_input:input-1',
    answers: expectedAnswers,
    operation_id: expect.stringMatching(/^answer-[a-f0-9]{64}$/),
  });
  expect(await answered.json()).toEqual({ fate: 'applied' });
  const receipts = await fetch(server.base + `/api/test/signals/${conversationId}/answers`);
  const receiptsText = await receipts.text();
  expect(receipts.ok, receiptsText).toBe(true);
  expect(JSON.parse(receiptsText)).toEqual({ 'input-1': expectedAnswers });
  const events = (await getJson(server, `/api/conversation/conversations/${conversationId}/events`)).events;
  expect(
    events.filter((event: JsonObject) => event.kind === 'user_input_answered').map((event: JsonObject) => event.payload),
  ).toEqual([{
    request_id: '1:user_input:input-1',
    answers: expectedAnswers,
    operation_id: submitted.postDataJSON().operation_id,
  }]);
  await page.waitForSelector('[data-conversation-user-input-state="answered"]', { timeout: WAIT_MS });
  expect(await page.locator('[data-conversation-user-input-answers]').innerText()).toContain(
    'Recorded event replay',
  );
  // A new document consumes the durable answered event, not local submitted state.
  await page.reload();
  await page.waitForSelector('[data-conversation-rest-bar]', { timeout: WAIT_MS });
  await openConversationLayer(page);
  await page.locator('[data-conversation-lens-choice="full"]').click();
  await page.waitForSelector('[data-conversation-user-input-state="answered"]', { timeout: WAIT_MS });
  expect(await page.locator('[data-conversation-user-input-answers]').innerText()).toContain(
    'Recorded event replay',
  );
  await expect(page.locator('[data-user-input-panel]')).toHaveCount(0);
});

test('test_canonical_queue_writes_refresh_the_mounted_pane', async ({ signalServer, newContext }) => {
  // Actual held IDs reach real writers and refresh the mounted pane.
  //
  // The owner moved same-profile multi-tab transport coverage to its own Ticket.
  // The original two-tab case and failure evidence remain runnable in that Ticket.
  const server = signalServer;
  const conversationId = 'e2e-ticket-canonical-queue';
  const ticketId = await createTicketConversation(server, {
    conversationId,
    title: 'Manage held prompts',
  });
  const path = `/api/conversation/conversations/${conversationId}`;
  await post(server, `/api/test/signals/${conversationId}/queue`);
  const view = await getJson(server, path);
  const held = view.held_prompts;
  expect(held.map((row: JsonObject) => row.text)).toEqual(['queued message 1', 'queued message 2']);
  const [discardId, promoteId] = held.map((row: JsonObject) => row.held_prompt_id);
  const context = await newContext();
  const page = await openTicketConversation(server, ticketId, context, '[data-conversation-held-stack] [data-conversation-held-row]');
  const queue = page.locator('[data-conversation-held-stack]');
  await expect(queue.locator('[data-conversation-held-row]')).toHaveCount(2);
  await captureSuccess(page, 'ticket-busy-queue.png');
  const discardedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/held-prompts/${discardId}`) && response.request().method() === 'DELETE',
  );
  // A queued row's actions sit in its "more" menu, which opens first.
  const discardControl = page.locator(`[data-conversation-held-discard="${discardId}"]`);
  await page.locator('[data-conversation-held-row]', { has: discardControl }).locator('[data-conversation-held-more]').click();
  await captureSuccess(page, 'ticket-busy-queue-actions.png');
  await discardControl.click();
  const discarded = await discardedResponse;
  expect(await discarded.json()).toEqual({ discarded: true });
  await expect(page.locator('[data-conversation-held-row]')).toHaveCount(1, { timeout: WAIT_MS });
  expect((await getJson(server, path)).held_prompts[0].held_prompt_id).toBe(promoteId);
  const promotedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/held-prompts/${promoteId}/promote`) && response.request().method() === 'POST',
  );
  const promoteControl = page.locator(`[data-conversation-held-promote="send_now"][data-held-prompt-id="${promoteId}"]`);
  await page.locator('[data-conversation-held-row]', { has: promoteControl }).locator('[data-conversation-held-more]').click();
  await promoteControl.click();
  const promoted = await promotedResponse;
  expect(promoted.request().postDataJSON()).toEqual({ mode: 'send_now' });
  expect(await promoted.json()).toEqual({ promoted: true, fate: 'started' });
  await expect(page.locator('[data-conversation-held-row]')).toHaveCount(0, { timeout: WAIT_MS });
  expect((await getJson(server, path)).held_prompts).toEqual([]);
  const events: JsonObject[] = (await getJson(server, path + '/events')).events;
  expect(
    events.some(
      (event) => event.kind === 'prompt_discarded' && event.payload.sender_message_id === 'sender-1',
    ),
  ).toBe(true);
  expect(
    events.some(
      (event) =>
        event.kind === 'prompt' &&
        event.payload.sender_message_id === 'sender-2' &&
        event.payload.mode === 'send_now',
    ),
  ).toBe(true);

  const sendPath = `**/api/tickets/${ticketId}/conversation/send`;
  const field = page.locator('[data-conversation-input]');
  const send = page.locator('[data-conversation-send]');
  const transcript = page.locator('[data-conversation-content]');
  for (const [mode, words, fate] of [
    ['steer', 'Add this to the running turn.', 'injected'],
    ['send_now', 'Start this in a new turn now.', 'started'],
    ['queue', 'Send this to the idle agent.', 'started'],
  ] as const) {
    await field.fill(words);
    if (mode === 'queue') {
      await post(server, `/api/test/signals/${conversationId}/finish`);
      await expect.poll(async () => (await getJson(server, path)).is_running).toBe(false);
      await expect(page.locator('[data-conversation-send-mode-trigger]')).toHaveCount(0);
    } else {
      await page.locator('[data-conversation-send-mode-trigger]').click();
      await page.locator(`[data-conversation-send-mode-choice="${mode}"]`).click();
    }
    const release = Promise.withResolvers<void>();
    await page.route(sendPath, async route => {
      await release.promise;
      await route.continue();
    }, { times: 1 });
    const response = page.waitForResponse(result => result.url().endsWith(`/tickets/${ticketId}/conversation/send`));
    try {
      await send.click();
      const pending = transcript.locator('[data-conversation-outgoing]').filter({ hasText: words });
      await expect(pending).toBeVisible();
      await expect(queue).toHaveCount(0);
      await expect(field).toHaveValue('');
      await captureSuccess(page, `ticket-${mode === 'queue' ? 'idle-send' : mode}.png`);
    } finally {
      release.resolve();
    }
    expect((await (await response).json()).fate).toBe(fate);
  }

  const failed = 'This message receives a confirmed refusal.';
  await page.route(sendPath, route => route.fulfill({
    json: { conversation_id: conversationId, fate: 'refused', refusal_reason: 'write_to_backend_failed' },
  }), { times: 1 });
  await field.fill(failed);
  await send.click();
  const failure = transcript.locator('[data-conversation-outgoing]').filter({ hasText: failed });
  await expect(failure.locator('[data-conversation-status-next]')).toHaveText('Not delivered. The agent did not receive it.');
  await expect(failure.getByRole('button', { name: 'Try Again' })).toBeVisible();
  await expect(failure.getByRole('button', { name: 'Edit' })).toBeVisible();
  await captureSuccess(page, 'ticket-failed-send.png');
});

test('test_private_credential_save_survives_reload_and_reaches_the_actual_wire', async ({
  signalServer,
  newContext,
}) => {
  // Only browser → private owner API → encrypted store → live card proves this wiring.
  //
  // Reuse the existing Signal host and backend wire. Component tests cover the state
  // matrix; this one case covers the connected save, durable replay and actual delivery.
  const server = signalServer;
  const conversationId = 'e2e-ticket-private-credential';
  const ticketId: string = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: 'Supply an API key',
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id;
  await post(server, `/api/test/signals/${conversationId}/start`, { ticket_id: ticketId });
  const requestId = randomUUID();
  const requestedName = 'Panels_provider_production';
  const response = await fetch(server.base + '/api/credentials/requests', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(await agentHeaders(server, 'worker', ticketId, { conversationId })),
    },
    body: JSON.stringify({
      request_id: requestId,
      name: requestedName,
      provider: 'Provider',
      use: 'Read the authorized production measurement',
      auth_kind: 'bearer',
      header_name: null,
      help: 'Create a read key in the provider settings.',
      help_url: 'https://example.com/api-keys',
      replacement: null,
    }),
  });
  const responseText = await response.text();
  expect(response.status, responseText).toBe(200);
  expect(JSON.parse(responseText).state).toBe('pending');
  await post(server, `/api/test/signals/${conversationId}/finish`);
  const context = await newContext();
  const cardSelector = `[data-credential-request="${requestId}"]`;
  const page = await openTicketConversation(server, ticketId, context, cardSelector);
  const card = page.locator(cardSelector);
  await page.reload();
  await page.waitForSelector('[data-conversation-rest-bar]', { timeout: WAIT_MS });
  // Docked, the page reads no transcript: the waiting key request still shows on first load,
  // with no open and close of the conversation first.
  await expect(page.locator(`[data-conversation-ask-dock] ${cardSelector}`)).toBeVisible({ timeout: WAIT_MS });
  await openConversationLayer(page);
  await expect(card).toHaveAttribute('data-credential-state', 'pending', { timeout: WAIT_MS });
  // Three captures share this connected case; no repeated backend or state matrix.
  for (const [label, width, height] of [
    ['desktop', 1280, 900],
    ['phone', 390, 844],
    ['short', 320, 568],
  ] as const) {
    await page.setViewportSize({ width, height });
    await expect(card).toBeVisible({ timeout: WAIT_MS });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await captureSuccess(page, `credential-request-connected-${label}.png`);
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  const secret = 'synthetic-browser-owner-only-' + randomUUID().replaceAll('-', '');
  const posted: [string, string | null][] = [];
  page.on('request', (request) => posted.push([request.url(), request.postData()]));
  await card.getByRole('textbox', { name: 'Key name', exact: true }).fill(requestedName + '_edited');
  await card.getByLabel('API key', { exact: true }).fill(secret);
  const savedResponse = page.waitForResponse(
    (result) =>
      result.url().endsWith(`/credentials/requests/${requestId}/save`) && result.request().method() === 'POST',
  );
  await card.getByRole('button', { name: 'Save key', exact: true }).click();
  const saved = await savedResponse;
  await expectResponse(saved.status(), 200, saved);
  expect(saved.request().postDataJSON()).toEqual({
    name: requestedName + '_edited',
    value: secret,
    expected_revision: null,
  });
  const body = await saved.json();
  expect(body.state).toBe('saved');
  expect(body.delivery.status).toBe('started');
  expect(posted.filter(([, data]) => data && data.includes(secret)).map(([url]) => url)).toEqual([saved.url()]);
  await expect(card).toHaveAttribute('data-credential-state', 'saved', { timeout: WAIT_MS });
  await expect(card.locator('input')).toHaveCount(0);
  expect(await page.locator('[data-conversation-thread]').innerText()).not.toContain(secret);
  withDatabase(server, (database) =>
    database.read((sql) => {
      const metadata = read_metadata(sql, body.credential.id);
      expect(metadata.reference.name).toBe(requestedName + '_edited');
      expect(metadata.revision).toBe(1);
      expect(read_encrypted(sql, metadata.id).ciphertext.includes(Buffer.from(secret))).toBe(false);
    }),
  );
  const events = (await getJson(server, `/api/conversation/conversations/${conversationId}/events`)).events;
  expect(JSON.stringify(events)).not.toContain(secret);
  const delivered: string[] = (await getJson(server, `/api/test/signals/${conversationId}/delivered`)).texts;
  expect(delivered.length, JSON.stringify(delivered)).toBe(2);
  expect(delivered.at(-1)!.includes(requestId) && delivered.at(-1)!.includes('"outcome":"saved"')).toBe(true);
  expect(delivered.at(-1)).toContain(requestedName + '_edited');
  expect(delivered.join('')).not.toContain(secret);
  await post(server, `/api/test/signals/${conversationId}/finish`);
  await page.reload();
  await page.waitForSelector('[data-conversation-rest-bar]', { timeout: WAIT_MS });
  await openConversationLayer(page);
  await expect(card).toHaveAttribute('data-credential-state', 'saved', { timeout: WAIT_MS });
  await expect(card.locator('input')).toHaveCount(0);
  // The record is one system line: "<provider> API key · <name> · Saved".
  await expect(card.locator('[data-credential-record]')).toHaveText(/ · Saved$/, { timeout: WAIT_MS });
  await captureSuccess(page, 'credential-request-connected-saved.png');
});

test('test_web_link_in_agent_message_opens_in_a_new_tab_and_panels_keeps_its_place', async ({
  signalServer,
  newContext,
}) => {
  // A same-tab link leaves Panels, and Back then returns to the first screen. Only a real
  // browser shows which tab a click opens and whether the Panels tab stays where it was.
  const server = signalServer;
  const conversationId = 'e2e-ticket-web-link';
  const ticketId = await createTicketConversation(server, { conversationId, title: 'Ticket with a web link' });
  await post(server, `/api/test/signals/${conversationId}/message`, {
    kind: 'ticket',
    id: ticketId,
    label: 'Proof Worker',
    text: 'The test copy runs at [this address](https://outside.example.org/copy).',
  });
  const context = await newContext();
  await context.route('https://outside.example.org/**', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<title>Outside</title>' }),
  );
  const page = await openTicketConversation(server, ticketId, context, '.markdown-block a');
  const panelsUrl = page.url();
  const [outside] = await Promise.all([context.waitForEvent('page'), page.locator('.markdown-block a').click()]);
  await outside.waitForLoadState();
  expect(outside.url()).toBe('https://outside.example.org/copy');
  expect(page.url()).toBe(panelsUrl);
});
