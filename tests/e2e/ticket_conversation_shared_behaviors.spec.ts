/**
 * Ticket browser adapters through the real core and controlled backend.
 *
 * A mocked pane cannot prove that browser writes reach canonical queue/answer
 * writers, or that a mounted Ticket pane reflects the canonical result.
 * Only the backend wire is scripted. No installed provider participates.
 */
import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
import {
  WAIT_MS,
  agentHeaders,
  captureSuccess,
  cli,
  expect,
  test,
  withDatabase,
  type JsonObject,
  type ServerHandle,
} from './support/fixtures.ts';
import {
  createTicketConversation,
  getJson,
  navigateToRetainedHistory,
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
  await openConversationLayer(page);
  await page.waitForSelector('[data-user-input-panel]', { timeout: WAIT_MS });
  await page.locator('[data-conversation-lens-choice="full"]').click();
  await page.locator('[data-user-input-option="Composer"]').click();
  await page.locator('[data-user-input-option="Transcript"]').click();
  await page.locator('[data-user-input-continue]').click();
  await page.locator('[data-user-input-other]').fill('Recorded event replay');
  const submittedRequest = page.waitForRequest(
    (request) => request.url().endsWith('/user-input-answers') && request.method() === 'POST',
  );
  const answeredResponse = page.waitForResponse(
    (response) => response.url().endsWith('/user-input-answers') && response.request().method() === 'POST',
  );
  await page.locator('[data-user-input-continue]').click();
  const answered = await answeredResponse;
  const submitted = await submittedRequest;
  const expectedAnswers = {
    scope: { answers: ['Composer', 'Transcript'] },
    proof: { answers: ['Recorded event replay'] },
  };
  expect(submitted.postDataJSON()).toEqual({
    request_id: '1:user_input:input-1',
    answers: expectedAnswers,
  });
  expect(await answered.json()).toEqual({ fate: 'applied' });
  const receipts = await fetch(server.base + `/api/test/signals/${conversationId}/answers`);
  const receiptsText = await receipts.text();
  expect(receipts.ok, receiptsText).toBe(true);
  expect(JSON.parse(receiptsText)).toEqual({ 'input-1': expectedAnswers });
  const events = (await getJson(server, `/api/conversation/conversations/${conversationId}/events`)).events;
  expect(
    events.filter((event: JsonObject) => event.kind === 'user_input_answered').map((event: JsonObject) => event.payload),
  ).toEqual([{ request_id: '1:user_input:input-1', answers: expectedAnswers }]);
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
  const page = await openTicketConversation(server, ticketId, context, '[data-conversation-held-stack]');
  await expect(page.locator('[data-conversation-held-row]')).toHaveCount(2);
  const discardedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/held-prompts/${discardId}`) && response.request().method() === 'DELETE',
  );
  await page.locator(`[data-conversation-held-discard="${discardId}"]`).click();
  const discarded = await discardedResponse;
  expect(await discarded.json()).toEqual({ discarded: true });
  await expect(page.locator('[data-conversation-held-row]')).toHaveCount(1, { timeout: WAIT_MS });
  expect((await getJson(server, path)).held_prompts[0].held_prompt_id).toBe(promoteId);
  const promotedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/held-prompts/${promoteId}/promote`) && response.request().method() === 'POST',
  );
  await page
    .locator(`[data-conversation-held-promote="send_now"][data-held-prompt-id="${promoteId}"]`)
    .click();
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
});

const LONG_REPORT = Array.from(
  { length: 90 },
  (_, index) => `line${index} of a worker report as long as a real one`,
).join(' ');
// The thread's drawn rows, and the newest record sequence among them. Every read below
// needs both, so they are written once. These page scripts stay source text so all three
// share one copy; each is run as `(<source>)(<JSON argument>)`.
const THREAD_READS = String.raw`
  const thread = () => document.querySelector('[data-conversation-thread]');
  const nodes = () => {
    const root = thread();
    return root ? [...root.querySelectorAll('[data-conversation-view-key]')] : [];
  };
  const sequenceOf = (node) => {
    const found = /(?:^|:)e(\d+)$/.exec(node.dataset.conversationViewKey || '');
    return found ? Number.parseInt(found[1], 10) : null;
  };
  const newestRetained = () => {
    const found = nodes().map(sequenceOf).filter(value => value !== null);
    return found.length ? Math.max(...found) : null;
  };
  // Rows the record already held when the owner pressed Enter. One of these below the
  // owner's copy is the defect: their message placed inside history.
  const olderAfter = (all, node, target) => all.slice(all.indexOf(node) + 1)
    .filter(following => (sequenceOf(following) ?? Infinity) <= target)
    .map(following => following.dataset.conversationViewKey);
  const inFrame = (node) => {
    const frame = thread().getBoundingClientRect();
    const box = node.getBoundingClientRect();
    return box.bottom > frame.top + 1 && box.top < frame.bottom - 1;
  };
  const mineIn = (all, text) => all.filter(node => (node.textContent || '').includes(text));
`;
const NEWEST_RETAINED = '() => {' + THREAD_READS + '  return newestRetained();\n}';
// Watch the owner's own copy from the send onward. Position only: which row it is,
// whether any row follows it, and whether it stays in the thread's frame once it has
// been seen there. A correct final screenshot cannot show a transient burial, so every
// animation frame is sampled and the first violations are kept.
const WATCH_OWN_COPY =
  '({ text, target }) => {' +
  THREAD_READS +
  `
  const state = {
    started: performance.now(), violations: [], seen: false, shown: false,
    firstSeenMs: null, firstShownMs: null, caughtUpMs: null,
    pastHistoryDuringCatchUp: 0, newestRetained: null, copies: 0, mark: null, trail: [],
  };
  window.__ownCopy = state;
  const since = () => Math.round(performance.now() - state.started);
  const fault = (why, extra) => {
    if (state.violations.length < 20)
      state.violations.push({ atMs: since(), why, ...extra });
  };
  const tick = () => {
    const all = nodes();
    const mine = mineIn(all, text);
    const newest = newestRetained();
    state.newestRetained = newest;
    state.copies = mine.length;
    if (newest !== null && newest >= target && state.caughtUpMs === null)
      state.caughtUpMs = since();
    if (mine.length > 1)
      fault('drawn more than once', { copies: mine.length });
    const node = mine[0];
    const sample = { mark: node ? node.dataset.conversationViewKey : null, newest,
                     trailing: all.slice(-2).map(n => n.dataset.conversationViewKey) };
    const previous = state.trail[state.trail.length - 1];
    if (state.trail.length < 40 && (!previous || previous.mark !== sample.mark
        || previous.newest !== sample.newest
        || String(previous.trailing) !== String(sample.trailing)))
      state.trail.push({ atMs: since(), ...sample });
    if (node) {
      state.mark = node.dataset.conversationViewKey ?? null;
      if (!state.seen) { state.seen = true; state.firstSeenMs = since(); }
      const older = olderAfter(all, node, target);
      if (older.length)
        fault('history follows the owner copy', { after: older.slice(0, 3) });
      else if (state.caughtUpMs === null)
        state.pastHistoryDuringCatchUp += 1;
      if (inFrame(node)) {
        if (!state.shown) { state.shown = true; state.firstShownMs = since(); }
      } else if (state.shown) {
        fault('the owner copy left the thread frame', {});
      }
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}`;
const FINAL_PLACE =
  '({ text, target }) => {' +
  THREAD_READS +
  `
  const all = nodes();
  const mine = mineIn(all, text);
  return {
    copies: mine.length,
    historyAfter: mine.length === 1 ? olderAfter(all, mine[0], target) : null,
    inFrame: mine.length === 1 && inFrame(mine[0]),
    trailing: all.slice(-3).map(node => node.dataset.conversationViewKey),
  };
}`;

/** Call a page function kept as source text, as Python's evaluate(source, arg) does. */
function callSource(page: Page, source: string, argument?: unknown): Promise<any> {
  return page.evaluate(`(${source})(${argument === undefined ? '' : JSON.stringify(argument)})`);
}

async function recordLatestSequence(server: ServerHandle, conversationId: string): Promise<number> {
  const events = (await getJson(server, `/api/conversation/conversations/${conversationId}/events`)).events;
  return Number.parseInt(String(events.at(-1).sequence), 10);
}

/** Fill the conversation past the reader's retained bounds with unread work. */
async function seedLongWorkerReports(
  server: ServerHandle,
  conversationId: string,
  ticketId: string,
  count: number,
): Promise<void> {
  for (let index = 0; index < count; index++) {
    await post(server, `/api/test/signals/${conversationId}/message`, {
      label: 'Worker',
      kind: 'ticket',
      id: ticketId,
      text: `report ${index}: ${LONG_REPORT}`,
    });
  }
}

test('test_owner_send_lands_at_the_end_of_the_conversation_not_the_retained_window', async ({
  signalServer,
  newContext,
}) => {
  // The owner's own copy is the last row in the thread and stays in the frame.
  //
  // The risk is the owner's own message becoming invisible again. The symptom is
  // composed of reader coverage, the retained window's bounds, entry placement and
  // scroll together, so no unit test on the slot ledger can prove that the owner
  // sees it.
  const server = signalServer;
  const conversationId = 'e2e-ticket-own-send-placement';
  const ticketId = await createTicketConversation(server, {
    conversationId,
    title: 'Place an owner send',
  });
  await seedLongWorkerReports(server, conversationId, ticketId, 160);
  await post(server, `/api/test/signals/${conversationId}/finish`);
  const target = await recordLatestSequence(server, conversationId);

  const context = await newContext();
  const page = await openTicketConversation(
    server,
    ticketId,
    context,
    '[data-conversation-thread][data-conversation-placement-ready="true"]',
  );
  await navigateToRetainedHistory(page, target);
  const retainedAtEntry: number | null = await callSource(page, NEWEST_RETAINED);
  // Without this the test can pass for the wrong reason. A window that already holds
  // the newest row never places a send from the wrong end.
  expect(retainedAtEntry).not.toBeNull();
  expect(retainedAtEntry!, JSON.stringify([retainedAtEntry, target])).toBeLessThan(target);

  const text = 'OWN SEND placed at the end of the conversation';
  await callSource(page, WATCH_OWN_COPY, { text, target });
  await page.locator('[data-conversation-input]').fill(text);
  await page.locator('[data-conversation-input]').press('Enter');
  // Wait for the window to reach the record, which is the page-by-page catch-up that
  // buried the copy, then keep sampling for a moment while the thread settles. The
  // wait covers catch-up only, so a broken product fails an assertion below rather
  // than this call.
  await page.waitForFunction(() => (window as any).__ownCopy.caughtUpMs !== null, undefined, {
    timeout: WAIT_MS,
  });
  await page.waitForTimeout(1000);
  const watched = await page.evaluate(() => (window as any).__ownCopy);
  console.log('OWN_COPY_TRAIL ' + JSON.stringify(watched.trail));
  const shown = JSON.stringify(watched);
  expect(watched.seen, shown).toBe(true);
  expect(watched.shown, shown).toBe(true);
  expect(watched.violations, shown).toEqual([]);
  expect(watched.copies, shown).toBe(1);
  expect(watched.pastHistoryDuringCatchUp, shown).toBeGreaterThan(0);

  const prompts = (
    await getJson(server, `/api/conversation/conversations/${conversationId}/events`)
  ).events.filter(
    // Python matches the text in str(payload); the JSON text of the payload holds it the same way.
    (event: JsonObject) => event.kind === 'prompt' && JSON.stringify(event.payload).includes(text),
  );
  expect(prompts.length, JSON.stringify(prompts)).toBe(1);
  // The slot correlates its own record row, so the message is drawn once rather than
  // beside a second copy. It says nothing about placement: the drawn key is the row's
  // whatever order the slot ends up with.
  expect(watched.mark, shown).toBe(`e${prompts[0].sequence}`);
  const final = await callSource(page, FINAL_PLACE, { text, target });
  const finalShown = JSON.stringify(final);
  expect(final.copies, finalShown).toBe(1);
  expect(final.historyAfter, finalShown).toEqual([]);
  expect(final.inFrame, finalShown).toBe(true);
  // Only the turn the send itself started is drawn below the owner's message. This is
  // what catches a slot that keeps its minted order: by the time the record row exists
  // the mint reads a sequence past it, and the owner's message sorts below its own
  // turn anchor.
  expect(final.trailing.at(-1), finalShown).toBe(`turn:e${prompts[0].sequence}`);
  await captureSuccess(page, 'owner-send-placed-at-conversation-end.png');
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
  await card.getByRole('button', { name: 'Save', exact: true }).click();
  const saved = await savedResponse;
  expect(saved.status(), await saved.text()).toBe(200);
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
  await expect(card.getByText('Saved', { exact: true })).toBeVisible({ timeout: WAIT_MS });
  await captureSuccess(page, 'credential-request-connected-saved.png');
});
