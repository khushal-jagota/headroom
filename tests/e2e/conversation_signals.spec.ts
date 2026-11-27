/**
 * Prove runtime → change stream → mounted shared conversation marks.
 *
 * The material risk is that commit invalidation precedes settled runtime state,
 * or a consumer substitutes approval/child facts for its own conversation.
 * The owner requires one visible indicator: message takes precedence over work;
 * a read must reveal the spinner while the same actual work continues.
 * Neither a component test nor an API-only test exercises that complete boundary.
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Locator, Route } from '@playwright/test';
import {
  expectResponse,
  WAIT_MS,
  agentHeaders,
  api,
  bindConversation,
  captureSuccess,
  connectedMessage,
  expect,
  openPage,
  proposeAsWorker,
  test,
  ticketPrincipal,
  type JsonObject,
} from './support/fixtures.ts';
import { post } from './support/conversation.ts';

async function marks(root: Locator, working: boolean, message: boolean): Promise<void> {
  // Arguments are independent backend facts; presentation has one winner.
  for (const [kind, present] of [
    ['working', working && !message],
    ['message', message],
  ] as const) {
    await expect(root.locator(`[data-conversation-signal="${kind}"]`)).toHaveCount(Number(present), {
      timeout: WAIT_MS,
    });
  }
}

const WORKING_LINE = /^Working(?: for (?:[0-9]+h [0-9]+m|[0-9]+(?:s|m(?: [0-9]+s)?)))?$/;

test('test_exact_conversation_signals_cross_runtime_stream_and_surfaces', async ({
  signalServer,
  newContext,
}) => {
  // The Python gate is parametrized over layout; only "desktop" runs.
  const layout = 'desktop' as string;
  const server = signalServer;
  const evidence = join(dirname(server.dbPath), 'evidence');
  mkdirSync(evidence);
  const project = await api.post(server, '/api/projects', { name: 'Signal proof' });
  const item = await api.post(server, '/api/managers', {
    title: 'Signal manager',
    project_id: project.id,
  });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Signal Worker',
    worker_type: 'coding',
    brief: 'Agreed brief.',
    manager: { kind: 'manager', id: item.id },
  });
  await api.post(server, `/api/tickets/${ticket.id}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  const proposed = await fetch(server.base + `/api/tickets/${ticket.id}/propose`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(await agentHeaders(server, 'worker', ticket.id)) },
    body: JSON.stringify({ body: 'Quiet approval remains visible.', recap: 'Browser signal proof.' }),
  });
  await expectResponse(proposed.ok, true, proposed);
  const identities: Record<string, JsonObject> = {
    'proof-worker': { kind: 'ticket', id: ticket.id, label: 'Signal Worker' },
    'proof-supervisor': { kind: 'manager', id: item.id, label: 'Signal manager' },
    'proof-chief': { kind: 'chief', id: 'chief', label: 'Chief of Staff' },
  };
  for (const conversation of Object.keys(identities)) {
    await post(server, `/api/test/signals/${conversation}/start`);
    await post(server, `/api/test/signals/${conversation}/finish`);
  }
  await bindConversation(server, 'ticket', ticket.id, 'proof-worker');
  await bindConversation(server, 'agent', item.manager.agent_key, 'proof-supervisor');
  await bindConversation(server, 'agent', 'chief_of_staff', 'proof-chief');
  const context = await newContext();
  const page = await openPage(context, server, '#/workspace?view=items', '[data-screen="workspace"]');
  await page.setViewportSize({ width: layout === 'desktop' ? 1440 : 560, height: 1000 });
  const worker = page.locator(`.board-workspace-left [data-ticket-id="${ticket.id}"]`);
  const supervisor = page.locator(`[data-manager="${item.id}"] .item-head`);
  const chief = page.locator('[data-chief-destination]');
  const rows: Record<string, Locator> = {
    'proof-worker': worker,
    'proof-supervisor': supervisor,
    'proof-chief': chief,
  };

  async function rowMarks(conversation: string, row: Locator, working: boolean, message: boolean) {
    if (conversation === 'proof-worker') {
      // An unread message takes the mark column. Once read, the pending
      // proposal returns its Review mark, ahead of the working spinner.
      await expect(row.locator('[data-approval-mark="owner"]')).toHaveCount(Number(!message), {
        timeout: WAIT_MS,
      });
      await expect(row.locator('[data-conversation-signal="message"]')).toHaveCount(Number(message), {
        timeout: WAIT_MS,
      });
      await expect(row.locator('[data-conversation-signal="working"]')).toHaveCount(0, {
        timeout: WAIT_MS,
      });
    } else {
      await marks(row, working, message);
    }
  }

  async function paneMarks(pane: Locator, signal: Locator, working: boolean, message: boolean) {
    // A page title shows the unread dot but never the working spinner (redesign, owner):
    // the status line over the composer says Working while the dock is at rest.
    await expect(signal.locator('[data-conversation-signal="working"]')).toHaveCount(0, {
      timeout: WAIT_MS,
    });
    await expect(signal.locator('[data-conversation-signal="message"]')).toHaveCount(Number(message), {
      timeout: WAIT_MS,
    });
    // At rest the status line says Working, with an optional elapsed time.
    // An open conversation shows its Stop control instead.
    const state = await pane.locator('[data-conversation-pane]').getAttribute('data-conversation-state');
    if (state === 'rest') {
      const line = pane.locator('[data-conversation-rest-line]');
      if (working) await expect(line).toHaveText(WORKING_LINE, { timeout: WAIT_MS });
      else await expect(line).not.toHaveText(WORKING_LINE, { timeout: WAIT_MS });
    } else {
      await expect(pane.locator('[data-conversation-stop]')).toHaveCount(Number(working), {
        timeout: WAIT_MS,
      });
    }
  }

  for (const [conversation, row] of Object.entries(rows)) {
    await expect(row).toBeVisible({ timeout: WAIT_MS });
    await rowMarks(conversation, row, false, false);
  }
  await page.evaluate(() => {
    const w = window as any;
    w.signalProofEvents = [];
    w.signalProofStream = new EventSource('/api/changes');
    w.signalProofStream.onmessage = (event: MessageEvent) => w.signalProofEvents.push(event.data);
  });
  await page.waitForFunction(() => (window as any).signalProofStream.readyState === 1, undefined, {
    timeout: WAIT_MS,
  });
  const observations: JsonObject[] = [];
  const selected: Record<string, Locator> = layout === 'desktop' ? rows : { 'proof-worker': worker };
  for (const [conversation, row] of Object.entries(selected)) {
    // Rail popups are proved in Vitest. This connected host proves the row marks.
    const before: number = await page.evaluate('window.signalProofEvents.length');
    await post(server, `/api/test/signals/${conversation}/start`);
    if (layout === 'desktop') await rowMarks(conversation, row, true, false);
    await page.waitForFunction(
      (before: number) => (window as any).signalProofEvents.length > before,
      before,
      { timeout: WAIT_MS },
    );
    await post(server, `/api/test/signals/${conversation}/message`, identities[conversation]);
    await rowMarks(conversation, row, true, true);
    if (conversation === 'proof-worker') {
      await marks(supervisor, false, false);
      await marks(chief, false, false);
    }
    const pendingReads: Route[] = [];
    const deferRead = async (route: Route) => {
      pendingReads.push(route);
    };
    const readPattern = `**/api/conversation/conversations/${conversation}/owner-read`;
    await page.route(readPattern, deferRead);
    await row.click();
    const pane = page.locator('.board-workspace-right');
    const paneSignal = pane.locator(`[data-conversation-id="${conversation}"]`);
    await paneMarks(pane, paneSignal, true, true);
    await page.mouse.move(layout === 'desktop' ? 1400 : 540, 350);
    await expect(page.locator('[data-rail-hover-panel][aria-hidden="false"]')).toHaveCount(0);
    if (conversation === 'proof-worker' && layout === 'desktop') {
      // The proposal names its stage, then who approves it ("What Changes · Approver
      // User", redesign).
      const approval = page.locator('[data-approval-block]');
      await expect(approval.locator('h2')).toHaveText('What Changes');
      await expect(approval.locator('.approver')).toContainText('Approver');
      const delivered = await post(server, `/api/tickets/${ticket.id}/conversation/send`, {
        conversation_id: conversation,
        sender_label: 'Owner',
        mode: 'steer',
        content: [{ piece: 'text', text: 'An ordinary owner reply.' }],
      });
      expect(delivered).toEqual({ conversation_id: conversation, fate: 'injected' });
      // An addressed owner send acknowledges its captured record. A new explicit
      // message stays unread until the held browser acknowledgement reaches it.
      await paneMarks(pane, paneSignal, true, false);
      await post(server, `/api/test/signals/${conversation}/message`, identities[conversation]);
      await paneMarks(pane, paneSignal, true, true);
    }
    await captureSuccess(page, join(evidence, `${conversation}-${layout}-page-message.png`));
    const rest = pane.locator('[data-conversation-rest-bar]');
    if (await rest.isVisible()) {
      const request = page.waitForRequest(readPattern, { timeout: WAIT_MS });
      await pane.locator('[data-conversation-expand]').click();
      await request;
      await expect(page.locator('[data-conversation-state="opened"]')).toBeVisible();
    }
    expect(pendingReads.length, 'An eligible open conversation must acknowledge its message').toBeGreaterThan(0);
    for (const readRoute of [...pendingReads]) {
      observations.push({ conversation, browser_read: readRoute.request().postData() });
      await readRoute.continue();
    }
    await page.unroute(readPattern, deferRead);
    await paneMarks(pane, paneSignal, true, false);
    await rowMarks(conversation, row, true, false);
    await post(server, `/api/test/signals/${conversation}/finish`);
    await paneMarks(pane, paneSignal, false, false);
    await rowMarks(conversation, row, false, false);
    await page.evaluate("location.hash = '#/workspace?view=items'");
    await expect(row).toBeVisible({ timeout: WAIT_MS });
    await rowMarks(conversation, row, false, false);
  }
  const events: string[] = await page.evaluate('window.signalProofEvents');
  expect(events.length > 0 && [...new Set(events)].join() === 'change', JSON.stringify(events)).toBe(true);
  writeFileSync(
    join(evidence, 'observations.json'),
    JSON.stringify(
      {
        observations,
        change_events: events,
        layout,
        server: server.base,
        db_path: server.dbPath,
        backend: 'scripted adapter; real conversation core; real fixed refresh window',
      },
      null,
      2,
    ),
  );
  await page.evaluate('window.signalProofStream.close()');
});

test('test_combined_review_approval_reply_and_bounded_read_reach_canonical_writers', async ({
  signalServer,
  newContext,
}) => {
  // The combined card must preserve canonical approval, delivery, and read bounds.
  //
  // Reuses the existing isolated Signal host. Only its backend wire is scripted.
  // Component/API tests cannot prove that the browser section reaches the actual
  // proposal writer, backend wire, and bounded owner-read writer.
  const server = signalServer;
  const project = await api.post(server, '/api/projects', { name: 'Review message proof' });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Reply in review',
    worker_type: 'coding',
    brief: 'Approved task.',
    project_id: project.id,
    manager: { kind: 'chief', id: 'chief' },
  });
  const identity = String(ticket.id);
  await api.post(server, `/api/tickets/${identity}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  const note = join(dirname(server.dbPath), 'files', 'tickets', identity, 'note.html');
  mkdirSync(dirname(note), { recursive: true });
  writeFileSync(note, '<h1>Reviewed file</h1>', 'utf-8');
  const noteLink = `[the note](/files/tickets/${identity}/note.html)`;
  await proposeAsWorker(
    server,
    identity,
    `The proposal remains beside the conversation. See ${noteLink}.`,
  );
  const conversation = 'review-reply-proof';
  await post(server, `/api/test/signals/${conversation}/start`);
  await bindConversation(server, 'ticket', identity, conversation);
  const sender = ticketPrincipal(identity);
  await connectedMessage(
    server,
    conversation,
    sender,
    `Please answer this displayed message about ${noteLink}.`,
  );
  const context = await newContext();
  const page = await openPage(context, server, '#/review', '[data-review-card]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const ticketCard = page.locator(`[data-review-ticket-card][data-ticket-id="${identity}"]`);
  const card = ticketCard.locator('[data-review-item-type="message"]');
  await expect(card.locator('[data-review-message-sequence]')).toHaveCount(1, { timeout: WAIT_MS });
  const bound = Number.parseInt(
    (await card.locator('[data-review-message-sequence]').getAttribute('data-review-message-sequence')) ||
      '0',
    10,
  );
  const badge = page.locator('[data-review-badge]').first();
  await expect(badge).toHaveText('1');
  await expect(page.locator('[data-review-ticket-card]')).toHaveCount(1);
  const proposal = (await api.get(server, `/api/tickets?id=${identity}&detail=full`)).pending_proposal;
  await expect(ticketCard.locator('[data-accept]')).toBeVisible({ timeout: WAIT_MS });
  await captureSuccess(page, 'connected-review-desktop.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await captureSuccess(page, 'connected-review-narrow.png');
  await page.setViewportSize({ width: 1440, height: 1000 });
  // A file opens over its card. The bar under it belongs to what held the link: the
  // message's reply, or the proposal's decisions as the artifact's own foot, with no
  // room reserved between them. Only a real browser lays out that geometry.
  const artifact = page.locator('[data-ticket-artifact]');
  const bar = artifact.locator('[data-artifact-bar]');
  await card.locator('a[href*="note.html"]').click();
  await expect(bar.locator('[data-review-reply]')).toBeVisible();
  await expect(bar.locator('[data-accept]')).toHaveCount(0);
  await artifact.locator('[data-ticket-artifact-close]').click();
  await ticketCard.locator('[data-approval-block] a[href*="note.html"]').click();
  await expect(bar.locator('[data-accept]')).toBeVisible();
  await expect(bar.locator('[data-review-reply]')).toHaveCount(0);
  // The artifact rises into place. Its geometry is read once the rise has ended.
  await expect(page.locator('[data-deck-layer]')).toHaveCSS('transform', 'none');
  await expect(page.locator('[data-deck-layer]')).toHaveCSS('opacity', '1');
  const gap = await page.evaluate(
    () =>
      document.querySelector('[data-artifact-bar]')!.getBoundingClientRect().top -
      document.querySelector('.ticket-artifact-body')!.getBoundingClientRect().bottom,
  );
  expect(gap).toBe(0);
  // The decision is made from the bar, and it closes the file with it.
  await bar.locator('[data-accept]').click();
  await bar.locator('[data-ceiling-picker-choice="needs_implementation"]').click();
  const decisionUrl = `${server.base}/api/tickets/${identity}/decision-operations/`;
  const approvedResponse = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().startsWith(decisionUrl),
  );
  await bar.locator('[data-ceiling-picker-choice="owner"]').click();
  const approved = await approvedResponse;
  await expectResponse(approved.ok(), true, approved);
  const approvalBody = approved.request().postDataJSON();
  expect(approvalBody !== null && typeof approvalBody === 'object' && !Array.isArray(approvalBody)).toBe(
    true,
  );
  expect(approvalBody.decision).toBe('approve');
  expect(approvalBody.field).toBe('what_changes');
  expect(approvalBody.expected_proposal_revision).toBe(proposal.revision);
  const approvalResult = await approved.json();
  expect(approvalResult.receipt_contract).toBe(1);
  const receipt = approvalResult.receipt;
  expect(receipt.operation_id).toBe(approved.url().slice(decisionUrl.length));
  expect(receipt.operation_id).toBeTruthy();
  expect(receipt.ticket_id).toBe(identity);
  expect(receipt.decision).toBe('approve');
  expect(receipt.field).toBe('what_changes');
  expect(receipt.expected_proposal_revision).toBe(proposal.revision);
  expect(receipt.outcome).toBe('applied');
  expect(receipt.error).toBeNull();
  expect(receipt.transition.pending_proposal_revision).toBe(receipt.expected_proposal_revision);
  const canonical = await api.get(server, `/api/tickets?id=${identity}&detail=full`);
  for (const field of ['stage', 'ceiling', 'ceiling_holder', 'ticket_status']) {
    expect(receipt.transition[field]).toEqual(canonical[field]);
  }
  expect(canonical.field_values.what_changes).toBe(proposal.body);
  expect(canonical.pending_proposal).toBeNull();
  await expect(artifact).toHaveCount(0);
  await expect(ticketCard.locator('[data-approval-block]')).toHaveCount(0);
  await expect(card.locator('[data-review-message-sequence]')).toHaveCount(1);
  await expect(badge).toHaveText('1');
  await card.locator('[data-review-reply]').fill('The reply reaches this conversation once.');
  const submitted: JsonObject[] = [];

  await page.route(
    `**/api/tickets/${identity}/conversation/send`,
    async (route) => {
      const body = route.request().postDataJSON();
      expect(body !== null && typeof body === 'object' && !Array.isArray(body)).toBe(true);
      submitted.push(body);
      await connectedMessage(server, conversation, sender, 'This newer message stays unread.');
      await route.continue();
    },
    { times: 1 },
  );
  const sentResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/tickets/${identity}/conversation/send`),
  );
  await card.locator('[data-review-send]').click();
  const sent = await sentResponse;
  await expectResponse(sent.ok(), true, sent);
  expect(await sent.json()).toMatchObject({
    conversation_id: conversation,
    fate: 'accepted',
    message_id: submitted[0].sender_message_id,
  });
  expect(submitted[0].conversation_id).toBe(conversation);
  expect(submitted[0].owner_read_through_sequence).toBe(bound);
  let snapshot = await api.get(server, `/api/conversation/conversations/${conversation}`);
  expect(snapshot.owner_read_through_sequence).toBe(bound);
  // The agent is busy. A review reply steers into its running turn, so no queue holds it.
  await expect(card.locator('[data-review-outgoing]')).toHaveCount(0);
  await expect(card.locator('[data-review-reply]')).toHaveValue('');
  expect(snapshot.held_prompts).toEqual([]);
  await captureSuccess(page, 'review-reply-steered.png');
  await post(server, `/api/test/signals/${conversation}/finish`);
  // The card can now advance to the newer message, which the original receipt did not read.
  await expect(page.locator('[data-deck-leaving]')).toHaveCount(0);
  await expect(card.locator('[data-review-message-sequence]')).toHaveCount(1);
  await expect(card.locator('.review-message-run')).toHaveText('This newer message stays unread.');
  await expect(badge).toHaveText('1');
  await page.waitForFunction(
    async ({ conversation, identity }) => {
      const endpoint = `/api/conversation/conversations/${conversation}/events?after=0`;
      const response = await fetch(endpoint);
      const value = await response.json();
      const rows = Array.isArray(value) ? value : value.events;
      return rows.some(
        (row: any) => row.kind === 'prompt' && row.payload.sender_message_id === identity,
      );
    },
    { conversation, identity: submitted[0].sender_message_id as string },
    { timeout: WAIT_MS },
  );
  snapshot = await api.get(server, `/api/conversation/conversations/${conversation}`);
  expect(snapshot.owner_read_through_sequence).toBe(bound);
  // A durable prompt row is admission. This receipt proves backend delivery.
  const receipts: string[] = await page.evaluate(
    async ({ conversation, timeout }) => {
      const deadline = performance.now() + timeout;
      while (performance.now() < deadline) {
        const response = await fetch(`/api/test/signals/${conversation}/delivered`);
        const receipt = await response.json();
        const reply = 'The reply reaches this conversation once.';
        if (receipt.texts.some((text: string) => text.includes(reply))) return receipt.texts;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      throw new Error('The exact reply did not reach the scripted backend');
    },
    { conversation, timeout: WAIT_MS },
  );
  expect(receipts.filter((text) => text.includes('The reply reaches this conversation once.')).length).toBe(
    1,
  );
  const displayedBound = Number.parseInt(
    (await card.locator('[data-review-message-sequence]').getAttribute('data-review-message-sequence')) ||
      '0',
    10,
  );
  const reads: JsonObject[] = [];

  await page.route(
    `**/api/conversation/conversations/${conversation}/owner-read`,
    async (route) => {
      const body = route.request().postDataJSON();
      expect(body !== null && typeof body === 'object' && !Array.isArray(body)).toBe(true);
      reads.push(body);
      await connectedMessage(
        server,
        conversation,
        sender,
        'This message arrives after the displayed read boundary.',
      );
      await route.continue();
    },
    { times: 1 },
  );
  const readResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/conversations/${conversation}/owner-read`),
  );
  await card.locator('[data-review-mark-read]').click();
  const read = await readResponse;
  await expectResponse(read.ok(), true, read);
  expect(reads).toEqual([{ through_sequence: displayedBound }]);
  snapshot = await api.get(server, `/api/conversation/conversations/${conversation}`);
  expect(snapshot.owner_read_through_sequence).toBe(displayedBound);
  await expect(page.locator('[data-deck-leaving]')).toHaveCount(0);
  await expect(card.locator('[data-review-message-sequence]')).toHaveCount(1);
  await expect(card.locator('.review-message-run')).toHaveText(
    'This message arrives after the displayed read boundary.',
  );
  await expect(badge).toHaveText('1');
  await card.locator('[data-review-mark-read]').click();
  await expect(page.locator('[data-review-item-type=message]')).toHaveCount(0);
  await expect(page.locator('[data-review-ticket-card]')).toHaveCount(0);
  // A reset leaves the old unread run addressable. Open must not follow the new pointer.
  const replacement = 'review-replacement-proof';
  await post(server, `/api/test/signals/${replacement}/start`);
  await bindConversation(server, 'ticket', identity, replacement);
  await connectedMessage(server, conversation, sender, 'An unread message in the earlier conversation.');
  await page.reload();
  const historical = page.locator('[data-review-item-type="message"]');
  await expect(historical).toHaveAttribute('data-conversation-id', conversation);
  await historical.getByRole('link', { name: 'Open conversation', exact: false }).click();
  const pane = page.locator('[data-conversation-pane]');
  await expect(pane).toHaveAttribute('data-conversation-id', conversation);
  await expect(pane).toHaveAttribute('data-conversation-read-only', 'true');
  await expect(pane).toHaveAttribute('data-conversation-state', 'opened');
});
