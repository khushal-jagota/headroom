/** Browser coverage for managed ticket-file previews. */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Page } from '@playwright/test';
import {
  expectResponse,
  CHIEF,
  REPO_ROOT,
  WAIT_MS,
  api,
  bindConversation,
  cli,
  connectedMessage,
  expect,
  openPage,
  proposeAsWorker,
  test,
  type ServerHandle,
} from './support/fixtures.ts';

/** Page-side state these tests keep on `window`. */
type TestWindow = Window & {
  __retainedArtifactFrame?: Element;
  __retainedProposalArtifact?: Element | null;
  __artifactInPlaceMarker?: string;
  __artifactCompromised?: boolean;
};

/** Production hosts retain page state while conversation covers it. */
async function proveDockArtifactRetention(page: Page, label: string, evidence: string | undefined): Promise<void> {
  const artifact = page.locator('[data-ticket-artifact]');
  const iframe = artifact.locator('iframe[data-file-preview-html]');
  const frame = artifact.frameLocator('iframe[data-file-preview-html]');
  const pane = page.locator('[data-conversation-pane]');
  const draft = pane.locator('[data-conversation-input]');
  const settledDock = page
    .locator('[data-conversation-dock-state="rest"]:not([data-conversation-dock-transition])')
    .filter({ has: pane });
  if ((await pane.getAttribute('data-conversation-state')) === 'opened')
    await page.locator('[data-conversation-collapse]').click({ timeout: WAIT_MS });
  await expect(pane).toHaveAttribute('data-conversation-state', 'rest', { timeout: WAIT_MS });
  // The state changes before the dock motion releases the retained reader height.
  await expect(settledDock).toHaveCount(1, { timeout: WAIT_MS });
  await draft.fill('Keep this draft through full conversation');
  if ((await pane.getAttribute('data-conversation-state')) === 'opened')
    await page.locator('[data-conversation-collapse]').click({ timeout: WAIT_MS });
  await expect(pane).toHaveAttribute('data-conversation-state', 'rest');
  await expect(settledDock).toHaveCount(1, { timeout: WAIT_MS });
  await frame.locator('[data-retained-form]').fill('Keep this artifact value');
  await frame.locator('[data-bottom-control]').click({ timeout: WAIT_MS });
  await expect(frame.locator('[data-bottom-control]')).toHaveText('Bottom control works');
  await frame.locator('[data-retained-selection]').evaluate((node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
  const before = await frame.locator('body').evaluate(() => ({
    scroll: document.scrollingElement!.scrollTop,
    selection: getSelection()!.toString(),
  }));
  expect(before.scroll).toBeGreaterThan(0);
  await iframe.evaluate((node) => {
    (window as unknown as TestWindow).__retainedArtifactFrame = node;
  });
  const source = await iframe.getAttribute('src');
  const readerBounds = await artifact.boundingBox();
  expect(readerBounds).not.toBeNull();
  const readerHeight = readerBounds!.height;
  const bounds = await page.locator('.conversation-layer').boundingBox();
  expect(bounds).not.toBeNull();
  expect(readerBounds!.y + readerHeight).toBeLessThanOrEqual(bounds!.y + 1);
  if (evidence) await page.screenshot({ path: join(evidence, `${label}-dock.png`), fullPage: true });
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  await expect(pane).toHaveAttribute('data-conversation-state', 'opened', { timeout: WAIT_MS });
  if (evidence) await page.screenshot({ path: join(evidence, `${label}-full.png`), fullPage: true });
  await page.locator('[data-conversation-collapse]').click({ timeout: WAIT_MS });
  await expect(pane).toHaveAttribute('data-conversation-state', 'rest', { timeout: WAIT_MS });
  await expect(settledDock).toHaveCount(1, { timeout: WAIT_MS });
  await expect(draft).toHaveValue('Keep this draft through full conversation');
  expect(
    await iframe.evaluate((node) => node === (window as unknown as TestWindow).__retainedArtifactFrame),
  ).toBe(true);
  expect(await iframe.getAttribute('src')).toBe(source);
  await expect(frame.locator('[data-retained-form]')).toHaveValue('Keep this artifact value');
  await expect(frame.locator('[data-bottom-control]')).toHaveText('Bottom control works');
  const after = await frame.locator('body').evaluate(() => ({
    scroll: document.scrollingElement!.scrollTop,
    selection: getSelection()!.toString(),
  }));
  expect(after).toEqual(before);
  const returnedBounds = await artifact.boundingBox();
  expect(returnedBounds).not.toBeNull();
  expect(returnedBounds!.height).toBe(readerHeight);
  await draft.fill('');
  // Focusing the field opens the phone conversation, including during cleanup.
  if ((await pane.getAttribute('data-conversation-state')) === 'opened')
    await page.locator('[data-conversation-collapse]').click({ timeout: WAIT_MS });
  await expect(settledDock).toHaveCount(1, { timeout: WAIT_MS });
}

function ticketFilesDir(server: ServerHandle, ticketId: string): string {
  return join(dirname(server.dbPath), 'files', 'tickets', ticketId);
}

function writeTicketFiles(server: ServerHandle, ticketId: string): void {
  const root = ticketFilesDir(server, ticketId);
  mkdirSync(join(root, 'notes'), { recursive: true });
  mkdirSync(join(root, 'images'), { recursive: true });
  mkdirSync(join(root, 'video'), { recursive: true });
  mkdirSync(join(root, 'audio'), { recursive: true });
  writeFileSync(
    join(root, 'notes', 'space name.md'),
    '# File Notes\n\nRendered **here**.\n\n' +
      `[Nested](/files/tickets/${ticketId}/notes/other.md)\n\n` +
      `[Self](/files/tickets/${ticketId}/notes/space%20name.md)`,
    'utf8',
  );
  writeFileSync(join(root, 'notes', 'other.md'), '# Other Notes\n\nFresh route content.', 'utf8');
  writeFileSync(join(root, 'notes', 'slow.md'), '# Slow preview', 'utf8');
  writeFileSync(
    join(root, 'page.html'),
    '<h1>HTML File</h1><script>window.parent.__ticketFileScriptRan = true;</script>',
    'utf8',
  );
  writeFileSync(
    join(root, 'images', 'pic.png'),
    Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAW0lEQVR4nO3PQQ0AIBDAsAP/' +
        'nuGNAvZoFSzZOjNnyNi1dwfgUQCeBOBJAI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJ' +
        'AI4E4EkAngTgSQCeBOBJAI4E4EkAngTgSQCeBOBJAN4A2icCftL8UqQAAAAASUVORK5CYII=',
      'base64',
    ),
  );
  writeFileSync(join(root, 'video', 'demo.mp4'), Buffer.from('not a real mp4'));
  writeFileSync(join(root, 'audio', 'demo.mp3'), Buffer.from('not a real mp3'));
  writeFileSync(join(root, 'archive.bin'), Buffer.from('download me'));
}

/** Seed saved Markdown through the same proposal/accept writers as ordinary work. */
async function settleWhatChanges(server: ServerHandle, ticketId: string, body: string): Promise<void> {
  await proposeAsWorker(server, ticketId, body);
  const ticket = await api.post(server, `/api/tickets/${ticketId}/accept/what_changes`, {
    next_ceiling: 'none',
    next_holder: { kind: 'owner', id: 'owner' },
  });
  expect(ticket.stage).toBe('needs_implementation');
  expect(ticket.field_values.what_changes).toBe(body);
  expect(ticket.pending_proposal).toBeNull();
}

async function openTicketField(page: Page, field: string): Promise<void> {
  await page.locator('details[data-stage-fold]').evaluate((node) => {
    (node as HTMLDetailsElement).open = true;
  });
  const section = page.locator(`details[data-field="${field}"]`).first();
  await section.evaluate((node) => {
    const settledStages = node.closest('details[data-stage-fold]') as HTMLDetailsElement | null;
    if (settledStages) settledStages.open = true;
    (node as HTMLDetailsElement).open = true;
  });
  await page.locator(`details[data-field="${field}"][open]`).waitFor({ state: 'attached', timeout: WAIT_MS });
  await section.scrollIntoViewIfNeeded({ timeout: WAIT_MS });
}

test('test_html_artifact_interacts_loads_sibling_assets_and_refreshes_in_place', async ({
  railServer,
  newContext,
}) => {
  // The real browser resolves managed assets and refreshes over its Ticket.
  //
  // Mock DOM preparation and API bytes cannot prove browser execution,
  // sibling URL resolution, child navigation, or changed content in the open frame.
  // The same browser and server exercise both hosts against the shipped bundle.
  const server = railServer;
  const startedAt = performance.now();
  const ticketId = (
    await cli(server, ['ticket', 'create', '--input-json', '-'], {
      stdin: JSON.stringify({
        worker_type: 'coding',
        title: 'HTML artifact',
        brief: 'Agreed brief.',
        manager: { kind: 'chief', id: 'chief' },
      }),
      settleBrief: true,
    })
  ).id as string;
  writeTicketFiles(server, ticketId);
  const root = ticketFilesDir(server, ticketId);
  const previewDirectory = join(root, 'previews');
  mkdirSync(previewDirectory);
  writeFileSync(join(previewDirectory, 'style.css'), 'h1 { color: rgb(13, 71, 161); }', 'utf8');
  const nested = join(previewDirectory, 'nested');
  mkdirSync(nested);
  const child = join(nested, 'child.html');
  writeFileSync(
    child,
    '<h1>Child artifact</h1><a href="#detail">Jump to detail</a>' +
      '<a href="../../page.html">Outside directory</a>' +
      '<a href="../escape.html">Symlink escape</a>' +
      '<div style="height: 1400px"></div><h2 id="detail">Child detail</h2>' +
      '<script>try { window.parent.__artifactCompromised = true; ' +
      'document.body.dataset.parentAccess = "allowed"; } ' +
      'catch { document.body.dataset.parentAccess = "denied"; }</script>',
    'utf8',
  );
  const outside = join(root, 'outside.html');
  writeFileSync(outside, '<h1>Secret symlink bytes</h1>', 'utf8');
  symlinkSync(outside, join(previewDirectory, 'escape.html'));
  const documentPath = join(previewDirectory, 'index.html');
  writeFileSync(
    documentPath,
    '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head>' +
      '<body><h1>Initial artifact</h1><img src="../images/pic.png" alt="Sibling image">' +
      `<button onclick="this.textContent='Clicked'">Try interaction</button>` +
      '<a href="nested/child.html">Open child</a>' +
      '<a data-artifact-action="empty" href="" onclick="localAction(event)">Empty action</a>' +
      '<a data-artifact-action="hash" href="#" onclick="localAction(event)">Hash action</a>' +
      '<a data-artifact-action="none" onclick="localAction(event)">No href action</a>' +
      '<output data-artifact-action-count>0</output>' +
      '<script>let actionCount = 0; function localAction(event) { event.preventDefault(); ' +
      'document.querySelector("[data-artifact-action-count]").textContent = ++actionCount; }' +
      '</script><input data-retained-form aria-label="Artifact form">' +
      '<div style="height: 1600px"></div>' +
      '<p data-retained-selection>Retain this selection</p>' +
      `<button data-bottom-control onclick="this.textContent='Bottom control works'">` +
      'Bottom control</button>' +
      '</body></html>',
    'utf8',
  );
  const retainedDocument = readFileSync(documentPath, 'utf8');
  await settleWhatChanges(server, ticketId, `[HTML](/files/tickets/${ticketId}/previews/index.html)`);
  const ticketSelector = `section[data-screen="ticket"][data-ticket-id="${ticketId}"]`;
  const context = await newContext();
  const page = await openPage(context, server, `#/workspace/${ticketId}`, ticketSelector);
  // Prove this browser executed the module named by the committed production index.
  const dist = join(REPO_ROOT, 'web', 'dist');
  const module = /<script\s+type="module"[^>]*\ssrc="([^"]+)"/.exec(readFileSync(join(dist, 'index.html'), 'utf8'));
  expect(module).not.toBeNull();
  const moduleSrc = module![1]!;
  expect(await page.locator('script[type="module"]').getAttribute('src')).toBe(moduleSrc);
  const moduleUrl = server.base + moduleSrc;
  expect(
    await page.evaluate(
      (url) => performance.getEntriesByType('resource').some((entry) => entry.name === url),
      moduleUrl,
    ),
  ).toBe(true);
  // The entry starts the workspace through lazy chunks. Check the actual loaded bundle,
  // and match every served script to this build rather than assuming one output file.
  const scriptUrls = await page.evaluate((entryUrl) => [...new Set([
    entryUrl,
    ...performance.getEntriesByType('resource').map((entry) => entry.name).filter((name) => {
      const url = new URL(name);
      return url.origin === location.origin && url.pathname.startsWith('/_app/') && url.pathname.endsWith('.js');
    }),
  ])], moduleUrl);
  const scripts = await Promise.all(scriptUrls.map(async (url) => {
    const response = await context.request.get(url);
    expect(response.status()).toBe(200);
    const bytes = await response.body();
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    const distPath = new URL(url).pathname.slice('/_app/'.length);
    expect(sha256).toBe(createHash('sha256').update(readFileSync(join(dist, distPath))).digest('hex'));
    return { url, bytes, sha256 };
  }));
  expect(scripts.some(({ bytes }) => bytes.includes('Message the agent'))).toBe(true);
  for (const retiredLiteral of ['Steer the work', 'Everything', 'Newsreader'])
    expect(scripts.some(({ bytes }) => bytes.includes(retiredLiteral))).toBe(false);
  const servedSha256 = scripts.find(({ url }) => url === moduleUrl)!.sha256;
  const evidence = process.env.PANELS_TICKET_EVIDENCE_DIR || undefined;
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    writeFileSync(
      join(evidence, 'production-browser-asset.json'),
      JSON.stringify({ module_url: moduleUrl, sha256: servedSha256, scripts: scripts.map(({ url, sha256 }) => ({ url, sha256 })) }, null, 2) + '\n',
      'utf8',
    );
  }
  // Check the combined staging UI through rendered controls, never source strings.
  for (const retiredText of ['Steer the work', 'Everything', 'Newsreader'])
    await expect(page.getByText(retiredText, { exact: true })).toHaveCount(0);
  await openTicketField(page, 'what_changes');
  await page.evaluate(() => {
    (window as unknown as TestWindow).__artifactInPlaceMarker = 'kept';
  });
  const htmlRequests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes(`/files/tickets/${ticketId}/`) && request.url().endsWith('.html'))
      htmlRequests.push(request.url());
  });
  await page.locator('a.file-preview-artifact-link:visible[href*="index.html"]').first().click();
  const artifactAddress =
    `${server.base}/#/workspace/${ticketId}` + `?source=ticket&ticket=${ticketId}&path=previews%2Findex.html`;
  await page.waitForURL(artifactAddress, { timeout: WAIT_MS });
  const artifact = page.locator('[data-ticket-artifact]');
  const iframe = artifact.locator('iframe[data-file-preview-html]');
  const frame = artifact.frameLocator('iframe[data-file-preview-html]');
  await frame.getByRole('heading', { name: 'Initial artifact' }).waitFor({ timeout: WAIT_MS });
  expect(await iframe.getAttribute('sandbox')).toBeNull();
  expect(await frame.locator('h1').evaluate((node) => getComputedStyle(node).color)).toBe('rgb(13, 71, 161)');
  expect(
    await frame.getByAltText('Sibling image').evaluate(
      (image: HTMLImageElement) =>
        new Promise<number>((resolve) => {
          const done = () => resolve(image.naturalWidth);
          if (image.complete) done();
          else {
            image.addEventListener('load', done, { once: true });
            image.addEventListener('error', done, { once: true });
          }
        }),
    ),
  ).toBe(64);
  await frame.getByRole('button', { name: 'Try interaction' }).click({ timeout: WAIT_MS });
  await frame.getByRole('button', { name: 'Clicked' }).waitFor({ timeout: WAIT_MS });
  const result = await frame.locator('body').evaluate(async () => {
    localStorage.setItem('artifact-proof', 'saved');
    const response = await fetch('/api/meta');
    return { stored: localStorage.getItem('artifact-proof'), status: response.status };
  });
  expect(result).toEqual({ stored: 'saved', status: 200 });

  // The browser, production route, served file and browser must agree on identity.
  // A fixture-only DOM check cannot prove this connected retention boundary.
  for (const [width, height, layout] of [
    [1440, 1000, 'desktop'],
    [390, 844, 'mobile'],
  ] as const) {
    await page.setViewportSize({ width, height });
    await proveDockArtifactRetention(page, `ticket-${layout}`, evidence);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });

  // Authored action anchors keep their handlers and never enter viewer navigation.
  const originalFrameSrc = await iframe.getAttribute('src');
  const requestsBeforeActions = [...htmlRequests];
  for (const [index, action] of ['empty', 'hash', 'none'].entries()) {
    const actionCount = index + 1;
    await frame.locator(`[data-artifact-action="${action}"]`).click({ timeout: WAIT_MS });
    await expect(frame.locator('[data-artifact-action-count]')).toHaveText(String(actionCount));
    expect(await iframe.getAttribute('src')).toBe(originalFrameSrc);
    expect(await artifact.locator('[data-html-back]').count()).toBe(0);
    expect(await artifact.locator('[data-html-navigation-notice]').count()).toBe(0);
    expect(page.url()).toBe(artifactAddress);
  }
  expect(htmlRequests).toEqual(requestsBeforeActions);

  const childResponse = await context.request.get(`${server.base}/files/tickets/${ticketId}/previews/nested/child.html`);
  expect(childResponse.status()).toBe(200);
  expect(childResponse.headers()['content-disposition']!.startsWith('inline;')).toBe(true);
  await frame.getByRole('link', { name: 'Open child' }).click({ timeout: WAIT_MS });
  await frame.getByRole('heading', { name: 'Child artifact', exact: true }).waitFor({ timeout: WAIT_MS });
  await expect(frame.locator('body')).toHaveAttribute('data-parent-access', 'allowed');
  expect(await page.evaluate(() => (window as unknown as TestWindow).__artifactCompromised)).toBe(true);
  await frame.getByRole('link', { name: 'Jump to detail' }).click({ timeout: WAIT_MS });
  await expect(frame.locator('#detail')).toBeInViewport({ timeout: WAIT_MS });
  await frame.getByRole('link', { name: 'Outside directory' }).click({ timeout: WAIT_MS });
  await frame.getByRole('heading', { name: 'HTML File' }).waitFor({ timeout: WAIT_MS });

  writeFileSync(join(root, 'page.html'), '<h1>Refreshed page</h1>', 'utf8');
  await artifact.locator('[data-ticket-artifact-refresh]').click({ timeout: WAIT_MS });
  await frame.getByRole('heading', { name: 'Refreshed page' }).waitFor({ timeout: WAIT_MS });
  expect(await page.evaluate(() => (window as unknown as TestWindow).__artifactInPlaceMarker)).toBe('kept');
  expect(await page.locator(ticketSelector).count()).toBe(1);
  expect(await artifact.isVisible()).toBe(true);

  writeFileSync(documentPath, '<h1>Standalone index</h1><a href="nested/child.html">Open child</a>', 'utf8');
  await page.goto(`${server.base}/#/preview?source=ticket&ticket=${ticketId}&path=previews%2Findex.html`);
  const standalone = page.locator('[data-file-preview-route]');
  const standaloneFrame = standalone.frameLocator('iframe[data-file-preview-html]');
  await standaloneFrame.getByRole('heading', { name: 'Standalone index' }).waitFor({ timeout: WAIT_MS });
  await standaloneFrame.getByRole('link', { name: 'Open child' }).click({ timeout: WAIT_MS });
  await standaloneFrame.getByRole('heading', { name: 'Child artifact' }).waitFor({ timeout: WAIT_MS });
  expect(await standalone.locator('iframe').getAttribute('sandbox')).toBeNull();

  await page.goto(`${server.base}/#/review`);
  await page.locator('[data-screen="review"] [data-deck-item]:not([data-deck-leaving]) [data-review-empty]').waitFor({ timeout: WAIT_MS });
  for (const retiredText of ['Everything', 'Newsreader'])
    await expect(page.getByText(retiredText, { exact: true })).toHaveCount(0);
  if (evidence) await page.screenshot({ path: join(evidence, 'combined-review-ui.png'), fullPage: true });

  // An empty Chief thread uses its first-message prompt. The lower-level default
  // agent placeholder is checked in the executed production module above.
  await page.goto(`${server.base}/#/workspace/chief-of-staff`);
  await expect(page.getByPlaceholder('Send the first message to start it...', { exact: true })).toBeVisible({
    timeout: WAIT_MS,
  });
  for (const retiredText of ['Steer the work', 'Everything', 'Newsreader'])
    await expect(page.getByText(retiredText, { exact: true })).toHaveCount(0);
  if (evidence) await page.screenshot({ path: join(evidence, 'combined-chief-ui.png'), fullPage: true });

  // Reuse this server and browser for the other hosts. No new provider run occurs.
  writeFileSync(documentPath, retainedDocument, 'utf8');
  const artifactLink = `[Dock artifact](/files/tickets/${ticketId}/previews/index.html)`;
  const project = await api.post(server, '/api/projects', { name: 'Dock artifact proof' });
  const manager = await api.post(server, '/api/managers', { title: 'Dock manager', project_id: project.id });
  const response = await context.request.patch(server.base + `/api/managers/${manager.id}`, {
    data: { goal: artifactLink },
  });
  await expectResponse(response.ok(), true, response);
  await page.goto(`${server.base}/#/workspace/manager/${manager.id}`);
  await page.locator('[data-goal-band]').waitFor({ timeout: WAIT_MS });
  await page.locator('[data-goal-band]').evaluate((node) => {
    (node as HTMLDetailsElement).open = true;
  });
  await page.locator('a.file-preview-artifact-link:visible[href*="index.html"]').first().click();
  for (const [width, height, layout] of [
    [1440, 1000, 'desktop'],
    [390, 844, 'mobile'],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page
      .locator('[data-ticket-artifact]')
      .frameLocator('iframe')
      .getByRole('heading', { name: 'Initial artifact' })
      .waitFor({ timeout: WAIT_MS });
    await proveDockArtifactRetention(page, `manager-${layout}`, evidence);
  }

  const chiefConversation = 'dock-chief-artifact-proof';
  await api.post(server, `/api/test/signals/${chiefConversation}/start`, {});
  await bindConversation(server, 'agent', 'chief_of_staff', chiefConversation);
  await connectedMessage(
    server,
    chiefConversation,
    CHIEF,
    '# The artifact is ready\n\n' + artifactLink + '\n\n- Inspect the bottom control\n- Keep the form value',
  );
  await page.goto(`${server.base}/#/workspace/chief-of-staff`);
  await expect(page.locator('[data-conversation-unread-preview]')).toContainText('The artifact is ready', {
    timeout: WAIT_MS,
  });
  await expect(page.locator('[data-conversation-rest-line]')).toHaveText(
    /^Working(?: · (?:[0-9]+h [0-9]+m|[0-9]+(?:s|m(?: [0-9]+s)?)))?$/,
    { timeout: WAIT_MS },
  );
  if (evidence) await page.screenshot({ path: join(evidence, 'chief-working-unread.png'), fullPage: true });
  await api.post(server, `/api/test/signals/${chiefConversation}/finish`, {});
  await expect(page.locator('[data-conversation-rest-line]')).toHaveText('Idle', { timeout: WAIT_MS });
  await expect(page.locator('[data-conversation-unread-preview]')).toContainText('Dock artifact');
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  await page.locator('a.file-preview-artifact-link:visible[href*="index.html"]').first().click();
  for (const [width, height, layout] of [
    [1440, 1000, 'desktop'],
    [390, 844, 'mobile'],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page
      .locator('[data-ticket-artifact]')
      .frameLocator('iframe')
      .getByRole('heading', { name: 'Initial artifact' })
      .waitFor({ timeout: WAIT_MS });
    await proveDockArtifactRetention(page, `chief-${layout}`, evidence);
  }
  if (evidence)
    writeFileSync(
      join(evidence, 'dock-connected-proof.json'),
      JSON.stringify(
        {
          hosts: ['Ticket', 'Manager', 'Chief'],
          viewports: ['1440x1000', '390x844'],
          retained: ['iframe identity', 'iframe source', 'form value', 'scroll', 'selection', 'draft'],
          bottom_control_accessible: true,
          elapsed_seconds: Math.round(performance.now() - startedAt) / 1000,
        },
        null,
        2,
      ) + '\n',
      'utf8',
    );
});

test('test_proposal_artifact_and_draft_stay_open_when_approval_moves_to_owner', async ({
  railServer,
  newContext,
}) => {
  // Proves the live server PATCH -> SSE change stream -> TicketRoute refetch ->
  // WorkerProposalCard + ArtifactPreview bar boundary in real Chromium. Lower layers
  // cannot prove that a background holder transfer leaves the open artifact DOM node
  // and bar draft untouched and submits the bumped proposal revision on approval.
  const server = railServer;
  const project = await api.post(server, '/api/projects', { name: 'Proposal retention proof' });
  const ticket = await api.post(server, '/api/tickets', {
    title: 'Keep proposal artifact open across holder transfer',
    worker_type: 'coding',
    brief: 'Agreed brief.',
    project_id: project.id,
    manager: { kind: 'chief', id: 'chief' },
  });
  const ticketId = String(ticket.id);
  await api.post(server, `/api/tickets/${ticketId}/accept/brief`, {
    next_ceiling: 'needs_what_changes',
    next_holder: CHIEF,
  });
  const filesRoot = ticketFilesDir(server, ticketId);
  mkdirSync(join(filesRoot, 'artifacts'), { recursive: true });
  writeFileSync(
    join(filesRoot, 'artifacts', 'proposal-note.md'),
    '# Proposal Evidence\n\nStays open across reviewer transfer.\n',
    'utf8',
  );
  await proposeAsWorker(
    server,
    ticketId,
    `Review [the proposal artifact](/files/tickets/${ticketId}/artifacts/proposal-note.md).`,
  );
  const before = await api.get(server, `/api/tickets?id=${ticketId}&detail=full`);
  const initialRevision = Number(before.pending_proposal.revision);

  const context = await newContext();
  const page = await openPage(context, server, `#/workspace/${ticketId}`, '[data-approval-block]');
  page.setDefaultTimeout(WAIT_MS);
  await page.setViewportSize({ width: 1440, height: 1000 });

  const approvalCard = page.locator('[data-approval-block]');
  await expect(approvalCard.locator('[data-reviewer-heading]')).toContainText('Chief');
  await approvalCard.locator('a[href*="proposal-note.md"]').click();

  const artifact = page.locator('[data-ticket-artifact]');
  const bar = artifact.locator('[data-artifact-bar]');
  await expect(artifact).toBeVisible({ timeout: WAIT_MS });
  await expect(bar).toBeVisible({ timeout: WAIT_MS });
  await expect(artifact.locator('[data-file-preview-markdown]')).toContainText(
    'Stays open across reviewer transfer.',
    { timeout: WAIT_MS },
  );

  const draftNote = 'Keep this note when approval moves to owner.';
  const noteInput = bar.locator('[data-revision-note]');
  await noteInput.fill(draftNote);

  await page.evaluate(() => {
    (window as unknown as TestWindow).__retainedProposalArtifact = document.querySelector('[data-ticket-artifact]');
  });

  // Transfer ceiling_holder from chief to owner while the proposal artifact and draft note are open.
  await api.patch(server, `/api/tickets/${ticketId}`, {
    ceiling_holder: { kind: 'owner', id: 'owner' },
  });

  const liveCard = page.locator('[data-worker-live-card]');
  await expect(liveCard).toHaveAttribute('data-proposal-revision', String(initialRevision + 1), {
    timeout: WAIT_MS,
  });
  await expect(approvalCard.locator('[data-reviewer-heading]')).toContainText('User');
  await expect(artifact).toBeVisible();
  await expect(bar).toBeVisible();
  await expect(noteInput).toHaveValue(draftNote);
  expect(
    await page.evaluate(
      () => document.querySelector('[data-ticket-artifact]') === (window as unknown as TestWindow).__retainedProposalArtifact,
    ),
  ).toBe(true);

  const evidence = process.env.PANELS_TICKET_EVIDENCE_DIR;
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    await page.screenshot({ path: join(evidence, 'proposal-artifact-after-transfer.png'), fullPage: true });
  }

  // Approve from the bar and confirm the bumped revision and preserved note reach the server.
  const decisionUrl = `${server.base}/api/tickets/${ticketId}/decision-operations/`;
  const approvedResponse = page.waitForResponse(
    (response) => response.request().method() === 'POST' && response.url().startsWith(decisionUrl),
  );
  await bar.locator('[data-accept]').click();
  await bar.locator('[data-ceiling-picker-choice="needs_implementation"]').click();
  await bar.locator('[data-scope-holder="owner"]').click();

  const approved = await approvedResponse;
  await expectResponse(approved.ok(), true, approved);
  const payload = approved.request().postDataJSON();
  expect(payload.decision).toBe('approve');
  expect(payload.field).toBe('what_changes');
  expect(payload.message).toBe(draftNote);
  expect(payload.expected_proposal_revision).toBe(initialRevision + 1);

  const result = await approved.json();
  expect(result.receipt.outcome).toBe('applied');
  await expect(artifact).toHaveCount(0, { timeout: WAIT_MS });

  const after = await api.get(server, `/api/tickets?id=${ticketId}&detail=full`);
  expect(after.stage).toBe('needs_implementation');
  expect(after.pending_proposal).toBeNull();
});
