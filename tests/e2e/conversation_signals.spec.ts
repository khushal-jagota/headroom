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
  WAIT_MS,
  agentHeaders,
  api,
  bindConversation,
  captureSuccess,
  cli,
  connectedMessage,
  expect,
  openPage,
  proposeAsWorker,
  test,
  ticketPrincipal,
  type JsonObject,
} from './support/fixtures.ts';
import { getJson, navigateToRetainedHistory, post } from './support/conversation.ts';

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

const WORKING_LINE = /^Working(?: · [0-9]+(?:s|m(?: [0-9]+s)?))?$/;

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
  expect(proposed.ok, await proposed.text()).toBe(true);
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
  await proposeAsWorker(server, identity, 'The proposal remains beside the conversation.');
  const conversation = 'review-reply-proof';
  await post(server, `/api/test/signals/${conversation}/start`);
  await bindConversation(server, 'ticket', identity, conversation);
  const sender = ticketPrincipal(identity);
  await connectedMessage(server, conversation, sender, 'Please answer this displayed message.');
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
  await ticketCard.locator('[data-accept]').click();
  await ticketCard.locator('[data-ceiling-picker-choice="needs_implementation"]').click();
  const decisionUrl = `${server.base}/api/tickets/${identity}/decision-operations/`;
  const approvedResponse = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().startsWith(decisionUrl),
  );
  await ticketCard.locator('[data-ceiling-picker-choice="owner"]').click();
  const approved = await approvedResponse;
  expect(approved.ok(), await approved.text()).toBe(true);
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
  expect(sent.ok(), await sent.text()).toBe(true);
  expect((await sent.json()).fate).toBe('queued');
  expect(submitted[0].conversation_id).toBe(conversation);
  expect(submitted[0].owner_read_through_sequence).toBe(bound);
  let snapshot = await api.get(server, `/api/conversation/conversations/${conversation}`);
  expect(snapshot.owner_read_through_sequence).toBe(bound);
  expect(
    snapshot.held_prompts.some(
      (prompt: JsonObject) => prompt.sender_message_id === submitted[0].sender_message_id,
    ),
  ).toBe(true);
  await expect(card.locator('[data-review-message-sequence]')).toHaveCount(1);
  await expect(card.locator('.review-message-run')).toHaveText('This newer message stays unread.');
  await expect(badge).toHaveText('1');
  await post(server, `/api/test/signals/${conversation}/finish`);
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
  expect(read.ok(), await read.text()).toBe(true);
  expect(reads).toEqual([{ through_sequence: displayedBound }]);
  snapshot = await api.get(server, `/api/conversation/conversations/${conversation}`);
  expect(snapshot.owner_read_through_sequence).toBe(displayedBound);
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

test('test_history_refused_row_keeps_unread_credit_until_latest_delivery', async ({
  signalServer,
  newContext,
}) => {
  // Real tail refusal must constrain canonical read credit and unread projections.
  //
  // Reuse the Signal host. Component tests cannot prove that a refused tail row
  // stays unread through the actual owner-read writer, navigation projection,
  // paging reconnect, and Latest delivery. The connected case costs about 25 seconds.
  const server = signalServer;
  const conversation = 'history-read-credit-proof';
  const ticket: string = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: 'History read credit proof',
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id;
  await post(server, `/api/test/signals/${conversation}/start`);
  await bindConversation(server, 'ticket', ticket, conversation);
  const body = Array.from(
    { length: 90 },
    (_, i) => `line${i} of a worker report that is as long as a real one`,
  ).join(' ');
  for (let index = 0; index < 160; index++) {
    await post(server, `/api/test/signals/${conversation}/message`, {
      label: 'Worker',
      kind: 'ticket',
      id: ticket,
      text: `report ${index}: ${body}`,
    });
  }
  await post(server, `/api/test/signals/${conversation}/finish`);
  const context = await newContext();
  await context.addInitScript(`window.creditFrames=[];window.creditStreams=0;
      window.creditPendingReads=0;
      const Original=window.EventSource;
      window.EventSource=class extends Original {
        constructor(url,options) {super(url,options);
          if(String(url).includes('/view/tail')) window.creditStreams++;
          this.addEventListener('changes',event=>{
            window.creditFrames.push(JSON.parse(event.data));
          });
        }
      };
      const fetchOriginal=window.fetch;
      window.fetch=async (...args)=>{
        const url=typeof args[0]==='string'?args[0]:args[0].url;
        const read=url.includes('/owner-read');
        if(read)window.creditPendingReads++;
        try{return await fetchOriginal(...args);}
        finally{if(read)window.creditPendingReads--;}
      };`);
  const page = await openPage(
    context,
    server,
    `#/workspace/${ticket}`,
    `[data-screen="ticket"][data-ticket-id="${ticket}"]`,
  );
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  await page.locator('[data-conversation-lens-choice="focus"]').click({ timeout: WAIT_MS });
  const thread = page.locator('[data-conversation-thread]');
  await expect(thread).toHaveAttribute('data-conversation-placement-ready', 'true', { timeout: WAIT_MS });
  expect(await page.evaluate("document.visibilityState === 'visible' && document.hasFocus()")).toBeTruthy();

  const snapshot = () => api.get(server, `/api/conversation/conversations/${conversation}`);

  async function waitRead(sequence: number): Promise<void> {
    await page.waitForFunction(
      async ({ id, sequence }) => {
        const response = await fetch(`/api/conversation/conversations/${id}`);
        return (await response.json()).owner_read_through_sequence === sequence;
      },
      { id: conversation, sequence },
      { timeout: WAIT_MS },
    );
  }

  function anchor(): Promise<unknown> {
    return thread.evaluate((t) => {
      const frame = t.getBoundingClientRect();
      const row = [...t.querySelectorAll<HTMLElement>('[data-conversation-view-key]')].find((n) => {
        const b = n.getBoundingClientRect();
        return b.bottom > frame.top && b.top < frame.bottom;
      });
      return row
        ? { key: row.dataset.conversationViewKey, offset: row.getBoundingClientRect().top - frame.top }
        : null;
    });
  }

  async function arrive(text: string): Promise<number> {
    await post(server, `/api/test/signals/${conversation}/message`, {
      label: 'Worker',
      kind: 'ticket',
      id: ticket,
      text,
    });
    const sequence = Number((await snapshot()).latest_sequence);
    await page.waitForFunction(
      (sequence: number) =>
        (window as any).creditFrames.some(
          (frame: any) =>
            frame.coverage.through_sequence >= sequence &&
            frame.upserts.some((row: any) => row.source_sequence === sequence) &&
            frame.scope_changes.some(
              (scope: any) => scope.scope.kind === 'timeline' && scope.item_keys.includes(`e${sequence}`),
            ),
        ),
      sequence,
      { timeout: WAIT_MS },
    );
    // Settle the Svelte effect and any owner-read fetch it starts.
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
    );
    await page.waitForFunction('window.creditPendingReads===0', undefined, { timeout: WAIT_MS });
    return sequence;
  }

  async function unreadProjection(expected: boolean): Promise<void> {
    const card = await getJson(
      server,
      '/api/tickets?' + new URLSearchParams({ detail: 'full', id: ticket }).toString(),
    );
    expect(card.conversation_signal.message_for_owner).toBe(expected);
    expect(card.awaiting_reply).toBe(expected);
    // tests.support.real_provider_application.navigation_query(conversation), inlined.
    const navigationQuery = {
      projection_version: 7,
      scope_generation: 1,
      locale: 'en-US',
      selected: null,
      selected_conversation_id: null,
      review_conversation_ids: [conversation],
      review_skipped_keys: [],
      review_priority_keys: [],
      windows: [],
    };
    const navigation = await getJson(
      server,
      '/api/navigation/view/snapshot?' +
        new URLSearchParams({ view: JSON.stringify(navigationQuery) }).toString(),
    );
    const record = navigation.content.records.find(
      (row: JsonObject) => row.kind === 'conversation' && row.id === conversation,
    ).value;
    expect(record.attention.durable.message_for_owner).toBe(expected);
    expect(record.attention.current.signal.message_for_owner).toBe(expected);
    const review = await getJson(server, '/api/review');
    const ticketCards = review.items.filter(
      (item: JsonObject) => item.review_item_type === 'ticket' && item.ticket_id === ticket,
    );
    expect(
      ticketCards
        .flatMap((card: JsonObject) => card.messages)
        .filter((message: JsonObject) => message.conversation_id === conversation).length,
    ).toBe(Number(expected));
    const badge = page.locator('.rail-foot [data-review-badge]');
    const visibleMarks = page.locator('[data-conversation-signal="message"]:visible');
    if (expected) {
      await expect(visibleMarks.first()).toBeVisible({ timeout: WAIT_MS });
      await expect(badge).toHaveText('1', { timeout: WAIT_MS });
      await expect(badge).toBeVisible({ timeout: WAIT_MS });
    } else {
      await expect(visibleMarks).toHaveCount(0, { timeout: WAIT_MS });
      await expect(badge).toHaveText('', { timeout: WAIT_MS });
      await expect(badge).not.toBeVisible({ timeout: WAIT_MS });
    }
  }

  async function latest(text: string, sequence: number): Promise<void> {
    await page
      .getByRole('button', { name: 'Jump to latest message', exact: true })
      .first()
      .click({ timeout: WAIT_MS });
    await expect(thread).toContainText(text, { timeout: WAIT_MS });
    await page.waitForFunction(
      () => {
        const t = document.querySelector('[data-conversation-thread]')!;
        return t.scrollHeight - t.clientHeight - t.scrollTop < 4;
      },
      undefined,
      { timeout: WAIT_MS },
    );
    await waitRead(sequence);
    await unreadProjection(false);
  }

  const initial = Number((await snapshot()).latest_sequence);
  await waitRead(initial); // Preserve the existing initial-entry read-credit behavior.
  await navigateToRetainedHistory(page, initial);
  let before = await anchor();
  const first = 'Unread until Latest delivers this refused history row.';
  const firstSequence = await arrive(first);
  expect(await thread.innerText()).not.toContain(first);
  expect(await anchor()).toEqual(before);
  await expect(page.getByRole('button', { name: 'Jump to latest message', exact: true }).first()).toBeVisible({
    timeout: WAIT_MS,
  });
  const actual = Number((await snapshot()).owner_read_through_sequence);
  console.log(`READ_CREDIT_REFUSAL initial=${initial} unique=${firstSequence} actual=${actual}`);
  expect(actual, 'A refused row must not advance canonical owner read credit').toBe(initial);
  await unreadProjection(true);
  await latest(first, firstSequence);

  const streams: number = await page.evaluate('window.creditStreams');
  await thread.hover();
  await page.mouse.wheel(0, -100000);
  await page.waitForFunction((before: number) => (window as any).creditStreams > before, streams, {
    timeout: WAIT_MS,
  });
  await expect(thread).toContainText('report 0:', { timeout: WAIT_MS });
  before = await anchor();
  const second = 'Unread after a real history page and tail reconnect.';
  const secondSequence = await arrive(second);
  expect(await thread.innerText()).not.toContain(second);
  expect(await anchor()).toEqual(before);
  expect(Number((await snapshot()).owner_read_through_sequence)).toBe(firstSequence);
  await unreadProjection(true);
  await latest(second, secondSequence);
  const final = 'A following reader admits this next row without a rebuild.';
  const finalSequence = await arrive(final);
  await expect(thread).toContainText(final, { timeout: WAIT_MS });
  await waitRead(finalSequence);
  await unreadProjection(false);
});
