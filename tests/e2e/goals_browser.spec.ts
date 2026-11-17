/**
 * Saved goal edits and conversations cross the browser, API, store and change stream.
 *
 * The shared scripted-backend server proves the actual first-message ownership path without
 * starting a provider. Unit tests cannot prove mounted queries receive external goal edits.
 */
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { APIResponse, Locator, Route } from '@playwright/test';
import {
  expectResponse,
  WAIT_MS,
  agentHeaders,
  api,
  cli,
  expect,
  openPage,
  test,
  type JsonObject,
} from './support/fixtures.ts';

test('test_formula_expectation_projection_and_correction_reach_mounted_chart', async ({ server, newContext }) => {
  // Prove API/CLI projections cross Svelte, React and live resource invalidation.
  //
  // Pure projection and renderer tests cannot prove that a formula-only dependency's
  // manual correction reaches a mounted saved page. Reuse the ordinary server and
  // browser host for one scenario; no source, script or provider is necessary.
  const project = await api.post(server, '/api/projects', { name: 'Formula browser proof' });

  const manualMeasure = (name: string): Promise<JsonObject> =>
    api.post(server, '/api/measures', {
      project_id: project.id,
      name,
      unit: 'items',
      origin: 'manual',
      kind: 'reading',
      timezone: 'UTC',
    });

  const replace = async (measure: JsonObject, day: string, value: number): Promise<void> => {
    const state = await api.get(server, `/api/measures/${measure.id}/manual-state?date=${day}`);
    await api.post(server, `/api/measures/${measure.id}/manual-writes`, {
      request_id: `${measure.id}:${day}:${state.write_revision}`,
      date: day,
      basis: 'date',
      operation: 'replace',
      value,
      expected_definition_revision: state.definition_revision,
      expected_write_revision: state.write_revision,
    });
  };

  const actual = await manualMeasure('Progress');
  const baseline = await manualMeasure('Opening baseline');
  await replace(baseline, '2026-07-01', 100.125);
  for (const day of ['2026-07-02', '2026-07-03', '2026-07-04']) await replace(actual, day, 101.125);
  const fixedBaseline = { op: 'reference_at', measure_id: baseline.id, date: '2026-07-01' };
  const goal = await api.post(server, '/api/goals', {
    project_id: project.id,
    title: 'Follow the phased expectation',
    kind: 'quantitative',
    completion_rule: {
      actual: { op: 'reference', measure_id: actual.id, day_offset: 0 },
      target: { op: 'constant', value: 999 },
      direction: 'atleast',
      first_date: '2026-07-01',
    },
    deadline: '2026-07-08',
    primary_chart: {
      type: 'chart',
      id: 'formula-progress',
      kind: 'line',
      title: 'Progress against expectation',
      measure_id: actual.id,
      format: { decimals: 2 },
      target: 999,
      expectation: {
        op: 'if_before',
        date: '2026-07-03',
        before: {
          op: 'reference',
          measure_id: baseline.id,
          day_offset: 0,
        },
        otherwise: {
          op: 'add',
          left: fixedBaseline,
          right: {
            op: 'if_before',
            date: '2026-07-05',
            before: {
              op: 'multiply',
              left: { op: 'constant', value: 0.25 },
              right: { op: 'elapsed_days', start: '2026-07-01' },
            },
            otherwise: { op: 'constant', value: 100 },
          },
        },
      },
    },
    page: {
      kind: 'headroom.page',
      version: 1,
      title: 'Saved approach',
      summary: 'Follow the dated expectation.',
      blocks: [],
    },
  });
  const path = `/api/goals/${goal.id}`;
  const chartId: string = goal.primary_chart.id;
  const readPath = path + '/readings?first_date=2026-07-01&last_date=2026-07-08';
  const readings = await api.get(server, readPath);
  expect(
    await cli(server, ['goal', 'read', goal.id, '--first-date', '2026-07-01', '--last-date', '2026-07-08']),
  ).toEqual(readings);
  const projection = readings.charts[chartId];
  const points: Record<string, JsonObject> = Object.fromEntries(
    projection.points.map((point: JsonObject) => [point.date, point]),
  );
  expect(projection.summary_date).toBe('2026-07-04');
  expect([
    points['2026-07-03'].expected.value,
    points['2026-07-03'].difference,
    points['2026-07-03'].meets_expectation,
  ]).toEqual([100.625, 0.5, true]);
  expect([
    points['2026-07-04'].expected.value,
    points['2026-07-04'].difference,
    points['2026-07-04'].meets_expectation,
  ]).toEqual([100.875, 0.25, true]);
  expect(points['2026-07-02'].expected.value).toBeNull();
  expect(points['2026-07-02'].meets_expectation).toBeNull();
  expect(points['2026-07-05'].break_before).toBe(true);
  expect(points['2026-07-08'].expected.value).toBe(200.125);
  expect(points['2026-07-08'].actual.value).toBeNull();
  const chartDates: string[] = (await api.get(server, path + '/readings')).charts[chartId].points.map(
    (point: JsonObject) => point.date,
  );

  const page = await openPage(await newContext(), server, `#/workspace/goal/${goal.id}`, '[data-goal-page]');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const chart = page.locator(`[data-chart-id="${chartId}"]`);
  const headline = chart.locator('.metric-value[data-current]');
  const plot = chart.getByRole('slider');
  const expectedTip = chart.locator('[data-chart-tooltip] [data-tooltip-series="target"]');
  await expect(headline).toHaveAttribute('data-current', '101.125', { timeout: WAIT_MS });
  await chart.getByRole('button', { name: 'All time', exact: true }).click({ timeout: WAIT_MS });
  await expect(headline).toHaveAttribute('data-current', '101.125', { timeout: WAIT_MS });
  // The production SVG must keep both the missing date and the phase jump open.
  await expect(chart.locator('[data-line="target"]')).toHaveAttribute('d', /M.*M.*M/, { timeout: WAIT_MS });
  // Keep keyboard inspection independent of pointer exits during focus scroll.
  await page.mouse.move(0, 0);
  await plot.focus({ timeout: WAIT_MS });
  await plot.press('End', { timeout: WAIT_MS });
  await expect(plot).toHaveAttribute('aria-valuetext', /2026-07-08.*Expected 200.13/, { timeout: WAIT_MS });
  await expect(expectedTip).toHaveText('Expected200.13', { timeout: WAIT_MS });
  await expect(headline).toHaveAttribute('data-current', 'missing', { timeout: WAIT_MS });
  for (let step = 0; step < 6; step++) await plot.press('ArrowLeft', { timeout: WAIT_MS });
  await expect(plot).toHaveAttribute('aria-valuetext', /2026-07-02.*Expected No reading/, { timeout: WAIT_MS });
  await expect(chart.locator('.comparison')).toHaveText('Expected reading unavailable', { timeout: WAIT_MS });
  expect(await chart.locator('.comparison').evaluate((node) => (node as HTMLElement).style.color)).toBe(
    'var(--text-muted)',
  );
  await expect(chart.locator('[data-dot="target"]')).toHaveCSS('display', 'none', { timeout: WAIT_MS });
  await plot.press('ArrowRight', { timeout: WAIT_MS });
  await expect(plot).toHaveAttribute('aria-valuetext', /2026-07-03.*Expected 100.63/, { timeout: WAIT_MS });
  await expect(expectedTip).toHaveText('Expected100.63', { timeout: WAIT_MS });
  // Pointer input resolves the same real date as keyboard input.
  const bounds = await plot.boundingBox();
  expect(bounds).not.toBeNull();
  // Python's list.index raises when the date is absent.
  const todayIndex = chartDates.indexOf('2026-07-04');
  expect(todayIndex).toBeGreaterThanOrEqual(0);
  const todayPosition = todayIndex / (chartDates.length - 1);
  await page.mouse.move(bounds!.x + bounds!.width * todayPosition, bounds!.y + bounds!.height / 2);
  await expect(plot).toHaveAttribute('aria-valuetext', /2026-07-04.*Expected 100.88/, { timeout: WAIT_MS });
  await expect(expectedTip).toHaveText('Expected100.88', { timeout: WAIT_MS });

  // The baseline exists only in the formula: its write must invalidate this page.
  await page.locator('[data-goal-page]').evaluate((node) => {
    (node as HTMLElement).dataset.formulaProof = 'retained';
  });
  const savedPage = await api.get(server, path);
  await replace(baseline, '2026-07-01', 102.125);
  await expect(expectedTip).toHaveText('Expected102.88', { timeout: WAIT_MS });
  await expect(chart.locator('.comparison')).toHaveText('1.75 below target', { timeout: WAIT_MS });
  const corrected = (await api.get(server, readPath)).charts[chartId].points[3];
  expect([corrected.expected.value, corrected.difference, corrected.meets_expectation]).toEqual([
    102.875,
    -1.75,
    false,
  ]);
  expect((await api.get(server, path)).page).toEqual(savedPage.page);
  expect((await api.get(server, path)).revision).toEqual(savedPage.revision);
  await expect(page.locator('[data-goal-page]')).toHaveAttribute('data-formula-proof', 'retained', {
    timeout: WAIT_MS,
  });
  await page.mouse.move(0, 0);
  await plot.press('Escape', { timeout: WAIT_MS });
  await expect(headline).toHaveAttribute('data-current', '101.125', { timeout: WAIT_MS });

  const evidence = process.env.PANELS_FORMULA_EVIDENCE_DIR;
  if (evidence) {
    mkdirSync(evidence, { recursive: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({ path: join(evidence, 'formula-desktop.png'), fullPage: true, timeout: WAIT_MS });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(headline).toHaveAttribute('data-current', '101.125', { timeout: WAIT_MS });
    await page.screenshot({ path: join(evidence, 'formula-phone.png'), fullPage: true, timeout: WAIT_MS });
  }
});

test('test_goal_page_persistence_external_refresh_and_owned_conversation', async ({ signalServer, newContext }) => {
  const server = signalServer;
  const project = await api.post(server, '/api/projects', { name: 'Goal browser proof' });
  // Another mounted holder must not dismiss Chief's goal previews.
  await api.post(server, '/api/managers', { title: 'Other holder', project_id: project.id });
  const source = await api.post(server, '/api/measure-sources', {
    name: 'Collected reading',
    project_id: project.id,
    account: 'browser-proof',
    responsible: { kind: 'chief', id: 'chief' },
  });
  const measure = await api.post(server, '/api/measures', {
    project_id: project.id,
    name: 'Draft words',
    unit: 'words',
    origin: 'taken',
    kind: 'reading',
    timezone: 'UTC',
    source_id: source.id,
  });

  const collect = async (value: number, limitation: string | null = null): Promise<string> => {
    const text =
      'export default async function ({context, record}) {\n' +
      'const output = context.required_outputs[0];\n' +
      `await record(output.measure_id, output.required_date, ${value}, ` +
      `{limitation: ${JSON.stringify(limitation)}});\n}`;
    const currentSource = await api.get(server, `/api/measure-sources/${source.id}`);
    const configured = await api.put(server, `/api/measure-sources/${source.id}/script`, {
      expected_revision: currentSource.source_revision,
      text,
    });
    const run = await api.post(server, `/api/measure-sources/${source.id}/runs`, {
      mode: 'collection',
      expected_script_revision: configured.active_script.revision,
    });
    await api.post(server, '/api/test/poll/sources', { dispatch: true });
    return String(run.id);
  };

  const firstRun = await collect(42);

  const acceptedManual = async (
    name: string,
    kind: string,
    { previousDate = false }: { previousDate?: boolean } = {},
  ): Promise<JsonObject> => {
    const body: JsonObject = {
      project_id: project.id,
      name,
      unit: 'items',
      origin: 'manual',
      kind,
      timezone: 'UTC',
    };
    if (kind === 'period_count') body.period = 'day';
    const manual = await api.post(server, '/api/measures', body);
    const currentState = await api.get(server, `/api/measures/${manual.id}/manual-state`);
    let day = String(currentState.current_date);
    if (previousDate) {
      const previous = new Date(`${day}T00:00:00Z`);
      previous.setUTCDate(previous.getUTCDate() - 1);
      day = previous.toISOString().slice(0, 10);
    }
    await api.post(server, `/api/measures/${manual.id}/manual-writes`, {
      request_id: name,
      date: day,
      basis: 'date',
      operation: 'replace',
      value: 12,
      expected_definition_revision: currentState.definition_revision,
      expected_write_revision: currentState.write_revision,
    });
    return manual;
  };

  const manualPeriod = await acceptedManual('Period fallback', 'period_count', { previousDate: true });
  const manualSnapshot = await acceptedManual('Snapshot daily control', 'reading');
  const goal = await api.post(server, '/api/goals', {
    project_id: project.id,
    title: 'Publish a field guide',
    kind: 'binary',
    primary_checklist_block_id: null,
    page: {
      kind: 'headroom.page',
      version: 1,
      title: 'Approach',
      summary: '',
      blocks: [
        {
          type: 'checklist',
          id: 'approach',
          title: 'Approach checklist',
          items: [
            { id: 'outline', label: 'Write the outline', state: 'open' },
            { id: 'review', label: 'Review the draft', state: 'open' },
          ],
        },
        {
          type: 'table',
          id: 'notes',
          columns: ['Task', 'Note'],
          rows: [
            ['Outline', 'First'],
            ['Review', 'Second'],
          ],
        },
        {
          type: 'chart',
          id: 'reading',
          kind: 'number',
          placement: 'supporting',
          title: 'Draft words',
          measure_id: measure.id,
        },
        {
          type: 'checklist',
          id: 'supporting-tasks',
          title: 'Supporting tasks',
          items: [{ id: 'source-notes', label: 'Keep source notes', state: 'open' }],
        },
        {
          type: 'chart',
          id: 'coverage-gauge',
          kind: 'gauge',
          placement: 'supporting',
          title: 'Collected gauge',
          measure_id: measure.id,
          target: 40,
        },
        {
          type: 'chart',
          id: 'manual-period-cumulative',
          kind: 'gauge',
          placement: 'supporting',
          title: 'Manual period fallback',
          measure_id: manualPeriod.id,
          target: 12,
          manual: { model: 'cumulative_target', increments: [1] },
        },
        {
          type: 'chart',
          id: 'manual-snapshot-daily',
          kind: 'bar',
          placement: 'supporting',
          title: 'Manual snapshot quota control',
          measure_id: manualSnapshot.id,
          target: 12,
          manual: { model: 'daily_quota', increments: [1] },
        },
      ],
    },
  });
  const path = `/api/goals/${goal.id}`;
  const chiefHeaders = await agentHeaders(server, 'chief');
  const seededChiefConversation = (await api.get(server, '/api/chief/conversation')).conversation_id;
  const seededChiefStatus = await api.get(server, `/api/conversation/conversations/${seededChiefConversation}`);
  expect(seededChiefStatus.is_running).toBe(false);
  const page = await openPage(await newContext(), server, `#/workspace/goal/${goal.id}`, '[data-goal-page]');
  const goalPatch = () =>
    page.waitForResponse((response) => response.url().endsWith(path) && response.request().method() === 'PATCH', {
      timeout: WAIT_MS,
    });
  await expect(page.getByRole('heading', { name: 'Publish a field guide' })).toBeVisible({ timeout: WAIT_MS });
  const savedResponse = goalPatch();
  await page.getByRole('checkbox', { name: 'Write the outline', exact: true }).click({ timeout: WAIT_MS });
  const saved = await savedResponse;
  expect(saved.ok()).toBe(true);
  expect((await api.get(server, path)).page.blocks[0].items[0].state).toBe('done');
  await page.reload();
  await page.waitForSelector('[data-goal-page]', { timeout: WAIT_MS });
  // Completed steps move into the saved disclosure instead of disappearing on reload.
  await page.locator('[data-group-toggle]').click({ timeout: WAIT_MS });
  await expect(page.getByRole('checkbox', { name: 'Write the outline', exact: true })).toBeChecked({
    timeout: WAIT_MS,
  });
  const markedDone = goalPatch();
  await page.getByRole('button', { name: 'Mark done', exact: true }).click({ timeout: WAIT_MS });
  await markedDone;
  let current = await api.get(server, path);
  expect(current.completed).toBe(true);
  expect(current.active).toBe(true);
  expect(
    (await api.get(server, '/api/goals?active=true')).goals.some((row: JsonObject) => row.id === goal.id),
  ).toBe(true);
  // Holder configuration crosses the API and change stream into the mounted page.
  const beforeSelection = current.page;
  await api.patch(server, path, { primary_checklist_block_id: 'approach' }, chiefHeaders);
  current = await api.get(server, path);
  expect(current.primary_checklist_block_id).toBe('approach');
  expect(current.page).toEqual(beforeSelection);
  expect(current.completed).toBe(false);
  const primary = page.getByRole('region', { name: 'Completion checklist', exact: true });
  await expect(primary.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 of 2 done', {
    timeout: WAIT_MS,
  });
  await expect(page.getByRole('button', { name: 'Mark done', exact: true })).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: 'Review the draft', exact: true })).toHaveCount(1);

  // The controlled browser fixture cannot prove that a date edit crosses the real
  // writer, saved page JSON, query invalidation, and a subsequent reload. Reuse
  // this selected-checklist scenario rather than another connected server/host.
  const persistItemDeadline = async (value: string | null): Promise<void> => {
    const before = await api.get(server, path);
    const dateSavedResponse = goalPatch();
    const field = primary.getByLabel('Deadline for Review the draft', { exact: true });
    await field.fill(value ?? '', { timeout: WAIT_MS });
    await field.press('Enter', { timeout: WAIT_MS });
    const dateSaved = await dateSavedResponse;
    expect(dateSaved.ok()).toBe(true);
    const body = dateSaved.request().postDataJSON();
    expect(typeof body === 'object' && body !== null && !Array.isArray(body)).toBe(true);
    expect(body.expected_revision).toBe(before.revision);
    const accepted = await api.get(server, path);
    const expectedPage = before.page;
    expectedPage.blocks[0].items[1].deadline = value;
    expect(accepted.page).toEqual(expectedPage);
    expect(accepted.revision).toBe(before.revision + 1);
    for (const key of ['completed', 'active', 'primary_checklist_block_id', 'deadline'])
      expect(accepted[key], key).toEqual(before[key]);
    await page.reload();
    await expect(primary.getByLabel('Deadline for Review the draft', { exact: true })).toHaveValue(value ?? '', {
      timeout: WAIT_MS,
    });
    await expect(primary.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 of 2 done', {
      timeout: WAIT_MS,
    });
  };

  await persistItemDeadline('2031-10-30');
  await persistItemDeadline('2031-11-02');
  await persistItemDeadline(null);
  // Reuse this connected edit to prove that another checklist cannot submit an
  // old displayed revision while the preceding write or its query refresh waits.
  const heldPatch: [Route, APIResponse][] = [];
  const heldReads: Route[] = [];
  let holdingRefresh = false;

  const holdPatch = async (route: Route): Promise<void> => {
    if (route.request().method() !== 'PATCH') {
      await route.fallback();
      return;
    }
    const response = await route.fetch();
    holdingRefresh = true;
    heldPatch.push([route, response]);
  };

  const holdGoalRefresh = async (route: Route): Promise<void> => {
    const key = new URL(route.request().url()).searchParams.get('key');
    if (key === path && holdingRefresh) heldReads.push(route);
    else await route.fallback();
  };

  const awaitIntercepted = async (ready: () => boolean): Promise<void> => {
    const deadline = performance.now() + WAIT_MS;
    while (!ready() && performance.now() < deadline) {
      // Yield to Playwright's event handling until the named request is held.
      await page.waitForTimeout(10);
    }
    expect(ready(), 'The expected goal request was not intercepted').toBe(true);
  };

  const primaryCheckbox = primary.getByRole('checkbox', { name: 'Review the draft', exact: true });
  await page.route('**' + path, holdPatch);
  await page.route('**/api/resource-body?*', holdGoalRefresh);
  try {
    await page.getByRole('checkbox', { name: 'Keep source notes', exact: true }).click({ timeout: WAIT_MS });
    await awaitIntercepted(() => heldPatch.length > 0);
    await expect(primaryCheckbox).toBeDisabled({ timeout: WAIT_MS });
    const [ordinaryRoute, ordinaryResponse] = heldPatch.pop()!;
    const ordinaryReply = goalPatch();
    await ordinaryRoute.fulfill({ response: ordinaryResponse });
    const ordinary = await ordinaryReply;
    expect(ordinary.ok()).toBe(true);
    await awaitIntercepted(() => heldReads.length > 0);
    await expect(primaryCheckbox).toBeDisabled({ timeout: WAIT_MS });
    holdingRefresh = false;
    for (const route of heldReads) await route.continue();
    heldReads.length = 0;
    await expect(primaryCheckbox).toBeEnabled({ timeout: WAIT_MS });
  } finally {
    holdingRefresh = false;
    for (const [route, heldResponse] of heldPatch) await route.fulfill({ response: heldResponse });
    for (const route of heldReads) await route.continue();
    await page.unroute('**' + path, holdPatch);
    await page.unroute('**/api/resource-body?*', holdGoalRefresh);
  }
  // An ordinary checklist saves without contributing to effective completion.
  current = await api.get(server, path);
  expect(current.page.blocks[3].items[0].state).toBe('done');
  expect(current.completed).toBe(false);
  const completedResponse = goalPatch();
  await primary.getByRole('checkbox', { name: 'Review the draft', exact: true }).click({ timeout: WAIT_MS });
  const completed = await completedResponse;
  expect(completed.ok()).toBe(true);
  expect((await api.get(server, path)).completed).toBe(true);
  await page.reload();
  await expect(primary.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '2 of 2 done', {
    timeout: WAIT_MS,
  });
  await primary.locator('[data-group-toggle]').click({ timeout: WAIT_MS });
  await expect(primary.getByRole('checkbox', { name: 'Review the draft', exact: true })).toBeChecked({
    timeout: WAIT_MS,
  });
  // The native State select saves the independent active state immediately.
  const inactiveResponse = goalPatch();
  await page.getByLabel('State', { exact: true }).selectOption({ label: 'Inactive' });
  const inactive = await inactiveResponse;
  expect(inactive.ok()).toBe(true);
  await expect(page.getByLabel('State', { exact: true })).toHaveValue('false', { timeout: WAIT_MS });
  current = await api.get(server, path);
  expect(current.completed).toBe(true);
  expect(current.active).toBe(false);
  const updatedPage = current.page;
  updatedPage.blocks[0].items[1].label = 'Review the updated draft';
  updatedPage.blocks[0].items[1].state = 'open';
  await api.patch(server, path, { page: updatedPage, active: true }, chiefHeaders);
  await expect(primary.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 of 2 done', {
    timeout: WAIT_MS,
  });
  expect((await api.get(server, path)).completed).toBe(false);
  await expect(page.getByLabel('State', { exact: true })).toHaveValue('true', { timeout: WAIT_MS });
  await expect(
    primary
      .getByRole('list', { name: 'Open items' })
      .getByRole('checkbox', { name: 'Review the updated draft', exact: true }),
  ).toBeVisible({ timeout: WAIT_MS });
  // The list response supplies the same saved item counts to cards and the table.
  await page.locator('button[data-chief-destination]').click({ timeout: WAIT_MS });
  const card = page.locator(`[data-goal-card="${goal.id}"]`);
  await expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 of 2 done', {
    timeout: WAIT_MS,
  });
  // The newly interactive overview must use the real goal writer.
  // Reuse this connected scenario: component tests cannot prove persisted API
  // acceptance, effective completion, or reload reads from a card-originated edit.
  const overviewRevision = (await api.get(server, path)).revision;
  const overviewSavedResponse = goalPatch();
  await card.getByRole('checkbox', { name: 'Review the updated draft', exact: true }).click({ timeout: WAIT_MS });
  const overviewSaved = await overviewSavedResponse;
  expect(overviewSaved.ok()).toBe(true);
  const overviewBody = overviewSaved.request().postDataJSON();
  expect(typeof overviewBody === 'object' && overviewBody !== null && !Array.isArray(overviewBody)).toBe(true);
  expect(overviewBody.expected_revision).toBe(overviewRevision);
  const overviewAccepted = await api.get(server, path);
  expect(overviewAccepted.revision).toBe(overviewRevision + 1);
  expect(overviewAccepted.page.blocks[0].items[1].state).toBe('done');
  expect(overviewAccepted.completed).toBe(true);
  await page.reload();
  await expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '2 of 2 done', {
    timeout: WAIT_MS,
  });
  await card.getByRole('button', { name: '2 completed', exact: true }).click({ timeout: WAIT_MS });
  const overviewReopenedResponse = goalPatch();
  await card.getByRole('checkbox', { name: 'Review the updated draft', exact: true }).click({ timeout: WAIT_MS });
  const overviewReopened = await overviewReopenedResponse;
  expect(overviewReopened.ok()).toBe(true);
  expect((await api.get(server, path)).completed).toBe(false);
  await expect(card.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '1 of 2 done', {
    timeout: WAIT_MS,
  });
  await page.locator('.rail-utilities a[data-screen="goals"]').click({ timeout: WAIT_MS });
  const row = page
    .getByRole('row')
    .filter({ has: page.getByRole('link', { name: 'Publish a field guide', exact: true }) });
  await expect(row.getByRole('cell').nth(1)).toHaveText('1 done', { timeout: WAIT_MS });
  await expect(row.getByRole('cell').nth(2)).toHaveText('2 items', { timeout: WAIT_MS });
  await row.getByRole('link', { name: 'Publish a field guide', exact: true }).click({ timeout: WAIT_MS });
  const reading = page.locator('[data-chart-id="reading"] [data-current]');
  await expect(reading).toHaveAttribute('data-current', '42', { timeout: WAIT_MS });
  expect((await api.get(server, `/api/measure-source-runs/${firstRun}`)).run.status).toBe('succeeded');
  await collect(52);
  await expect(reading).toHaveAttribute('data-current', '52', { timeout: WAIT_MS });
  // A same-value source correction must refresh its factual details without
  // suppressing the mounted gauge's meaningful comparison or animated state.
  const gaugeCard = page.locator('[data-chart-id="coverage-gauge"]');
  const gauge = gaugeCard.locator('figure[aria-label]');
  await gauge.scrollIntoViewIfNeeded({ timeout: WAIT_MS });
  await expect(gauge).toHaveClass(/.*success.*/, { timeout: WAIT_MS });
  await expect(gauge.locator('[role="meter"]')).toHaveAttribute('aria-valuetext', /On target/, { timeout: WAIT_MS });
  // Wait for the actual animated count to settle before a same-value correction.
  await expect(gauge.locator('span[class*="status"]')).toHaveText('On target', { timeout: WAIT_MS });
  await collect(52, 'The retained query covers only part of this reading.');
  const details = gaugeCard.locator('.observation-details');
  await details.locator('summary').click({ timeout: WAIT_MS });
  await expect(details).toContainText('The retained query covers only part of this reading.', { timeout: WAIT_MS });
  await expect(gaugeCard.locator('.comparison')).toHaveCount(0);
  await expect(gauge).toHaveClass(/.*success.*/, { timeout: WAIT_MS });
  await expect(gauge.locator('[role="meter"]')).toHaveAttribute('aria-valuetext', /On target/, { timeout: WAIT_MS });
  await expect(gauge.locator('span[class*="status"]')).toHaveText('On target', { timeout: WAIT_MS });
  const retained: JsonObject[] = (await api.get(server, path + '/readings')).values;
  expect(
    retained.some(
      (item) =>
        item.measure_id === measure.id &&
        item.value === 52 &&
        item.coverage_status === 'partial' &&
        item.limitation === 'The retained query covers only part of this reading.',
    ),
  ).toBe(true);

  // Accepted manual models do not establish measure semantics. The cumulative
  // count uses a prior date, while a reading accepts the daily control unchanged.
  const cumulative = page.locator('[data-chart-id="manual-period-cumulative"]');
  await expect(cumulative.getByRole('meter')).toHaveAttribute('aria-valuetext', '12 of 12, target reached', {
    timeout: WAIT_MS,
  });
  await expect(cumulative.locator('.completion')).toHaveText('Target reached', { timeout: WAIT_MS });
  await expect(cumulative.locator('.radial-fill')).toHaveAttribute('stroke', 'var(--accent-done)', {
    timeout: WAIT_MS,
  });
  const snapshot = page.locator('[data-chart-id="manual-snapshot-daily"]');
  await expect(snapshot.locator('.comparison')).toHaveCount(0);
  await expect(snapshot.locator('.card-heading > :not(h3)')).toHaveCount(0);
  await expect(snapshot.locator('.bar-pick').last().locator('.bar')).toHaveAttribute(
    'style',
    /background-color: var\(--accent-done\)/,
    { timeout: WAIT_MS },
  );
  expect((await api.get(server, path)).page.blocks[0].items[0].state).toBe('done');

  // Reuse this connected host to prove a real native title commit and reload.
  // Frontend-only cases cannot prove API acceptance and persisted browser reads.
  const beforeTitle = await api.get(server, path);
  const titleEditor = page.getByRole('textbox', { name: 'Goal title', exact: true });
  await titleEditor.fill('Owner title', { timeout: WAIT_MS });
  const titledResponse = goalPatch();
  await titleEditor.press('Enter', { timeout: WAIT_MS });
  const titled = await titledResponse;
  await expectResponse(titled.ok(), true, titled);
  expect(titled.request().postDataJSON()).toEqual({
    title: 'Owner title',
    expected_revision: beforeTitle.revision,
  });
  const acceptedTitle = await api.get(server, path);
  expect(acceptedTitle.title).toBe('Owner title');
  expect(acceptedTitle.page).toEqual(beforeTitle.page);
  await page.reload();
  await page.waitForSelector('[data-goal-page]', { timeout: WAIT_MS });
  await expect(titleEditor).toHaveText('Owner title', { timeout: WAIT_MS });

  // Several goals use rows. The sole goal uses its accepted primary display.
  const previewGoal = async (goalId: string): Promise<Locator> => {
    await page.locator('.manager-card.chief .identity').hover({ timeout: WAIT_MS });
    const row = page.locator(`[data-rail-hover-panel].visible [data-manager-goal-id="${goalId}"]`);
    await expect(row).toBeVisible({ timeout: WAIT_MS });
    return row;
  };

  // An external revision reaches the mounted page without replacing a focused
  // draft. A stale save refuses the captured revision before a fresh edit succeeds.
  await titleEditor.fill('Unaccepted local title', { timeout: WAIT_MS });
  await page.locator('[data-goal-page]').evaluate((node) => {
    (node as HTMLElement).dataset.nativeEditProof = 'retained';
  });
  current = await api.get(server, path);
  const reordered = current.page;
  reordered.blocks[1].rows.reverse();
  reordered.blocks[0].title = 'Chief checklist';
  const refreshed = page.waitForResponse(
    (reply) => {
      const url = new URL(reply.url());
      const keys = url.searchParams.getAll('key');
      return url.pathname === '/api/resource-body' && keys.length === 1 && keys[0] === path;
    },
    { timeout: WAIT_MS },
  );
  const remote = await api.patch(server, path, { title: 'Chief title', page: reordered }, chiefHeaders);
  await refreshed;
  await expect(titleEditor).toHaveText('Unaccepted local title', { timeout: WAIT_MS });
  // The preview reads the accepted checklist while the page retains its title draft.
  await page.locator('.manager-card.chief .identity').hover({ timeout: WAIT_MS });
  const previewChecklist = page.locator('[data-rail-hover-panel].visible [data-block="checklist"]');
  await expect(previewChecklist).toHaveAttribute('data-presentation', 'smallest', { timeout: WAIT_MS });
  await expect(previewChecklist.getByRole('heading', { name: 'Chief checklist', exact: true })).toBeVisible({
    timeout: WAIT_MS,
  });
  const acceptedItems: JsonObject[] = remote.page.blocks[0].items;
  const acceptedDone = acceptedItems.filter((item) => item.state === 'done').length;
  await expect(previewChecklist.getByRole('progressbar')).toHaveAttribute(
    'aria-valuetext',
    `${acceptedDone} of ${acceptedItems.length} done`,
    { timeout: WAIT_MS },
  );
  await expect(page.locator('[data-goal-page]')).toHaveAttribute('data-native-edit-proof', 'retained', {
    timeout: WAIT_MS,
  });
  const localTitleResponse = goalPatch();
  await titleEditor.press('Enter', { timeout: WAIT_MS });
  const localTitle = await localTitleResponse;
  await expectResponse(localTitle.status(), 409, localTitle);
  expect(localTitle.request().postDataJSON()).toEqual({
    title: 'Unaccepted local title',
    expected_revision: current.revision,
  });
  const error = page.locator('[data-goal-page] .goal-heading .error-line');
  await expect(error).toBeVisible({ timeout: WAIT_MS });
  await expect(titleEditor).toHaveText('Unaccepted local title', { timeout: WAIT_MS });
  expect(await api.get(server, path)).toEqual(remote);
  await titleEditor.press('Escape', { timeout: WAIT_MS });
  await expect(titleEditor).toHaveText('Chief title', { timeout: WAIT_MS });
  await expect(error).toHaveCount(0, { timeout: WAIT_MS });
  await titleEditor.fill('Unaccepted local title', { timeout: WAIT_MS });
  const freshTitleResponse = goalPatch();
  await titleEditor.press('Enter', { timeout: WAIT_MS });
  const freshTitle = await freshTitleResponse;
  await expectResponse(freshTitle.status(), 200, freshTitle);
  expect(freshTitle.request().postDataJSON()).toEqual({
    title: 'Unaccepted local title',
    expected_revision: remote.revision,
  });
  await expect(error).toHaveCount(0, { timeout: WAIT_MS });
  await expect(titleEditor).toHaveText('Unaccepted local title', { timeout: WAIT_MS });
  const acceptedLocal = await api.get(server, path);
  expect(remote.revision).toBe(current.revision + 1);
  expect(acceptedLocal.title).toBe('Unaccepted local title');
  expect(acceptedLocal.revision).toBe(remote.revision + 1);
  expect(acceptedLocal.page).toEqual(reordered);
  await expect(page.locator('[data-goal-page]')).toHaveAttribute('data-native-edit-proof', 'retained', {
    timeout: WAIT_MS,
  });
  // The field stays locked until the page reads the saved goal back, so the
  // old title cannot flash. Playwright fill fails at once on a locked field.
  await expect(titleEditor).toHaveAttribute('contenteditable', 'true', { timeout: WAIT_MS });
  await titleEditor.fill('Discard this unsaved title', { timeout: WAIT_MS });
  await titleEditor.press('Escape', { timeout: WAIT_MS });
  await expect(titleEditor).toHaveText('Unaccepted local title', { timeout: WAIT_MS });
  await expect(error).toHaveCount(0, { timeout: WAIT_MS });
  expect((await api.get(server, path)).revision).toBe(acceptedLocal.revision);
  await expect(page.locator('[data-block="table"] tbody tr').first()).toContainText('Second', { timeout: WAIT_MS });
  expect((await api.get(server, path)).page.blocks[1].rows).toEqual([
    ['Review', 'Second'],
    ['Outline', 'First'],
  ]);

  // The holder preview must subscribe to another active goal while this one is selected.
  const other = await api.post(server, '/api/goals', {
    project_id: project.id,
    title: 'Another active goal',
    kind: 'binary',
    primary_checklist_block_id: null,
  });
  // The connected host proves that page completion reaches the saved holder preview.
  const checkedResponse = goalPatch();
  await primary.getByRole('checkbox', { name: 'Review the updated draft', exact: true }).click({ timeout: WAIT_MS });
  const checked = await checkedResponse;
  expect(checked.ok()).toBe(true);
  expect((await api.get(server, path)).completed).toBe(true);
  const completedGoal = await previewGoal(String(goal.id));
  await expect(completedGoal.locator('small')).toContainText('Done', { timeout: WAIT_MS });
  await completedGoal.click({ timeout: WAIT_MS });
  await expect(primary.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '2 of 2 done', {
    timeout: WAIT_MS,
  });
  // The authenticated fixture already has a Chief conversation. Opening either
  // goal must reuse it without starting work or adding model/session events.
  expect((await api.get(server, '/api/chief/conversation')).conversation_id).toBe(seededChiefConversation);
  const idleChief = await api.get(server, `/api/conversation/conversations/${seededChiefConversation}`);
  expect(idleChief.is_running).toBe(false);
  expect(idleChief.latest_sequence).toBe(seededChiefStatus.latest_sequence);
  const otherMessage = await api.post(server, '/api/messages/send', {
    target: { kind: 'chief', id: 'chief' },
    message: 'Work on our goals',
  });
  const holderConversationId = otherMessage.conversation_id;
  const chief = page.locator('[data-chief-destination]');
  await expect(chief.locator('[data-conversation-signal="working"]')).toHaveCount(1, { timeout: WAIT_MS });
  // Goals retain no independent conversation marks, even when their holder works.
  await expect((await previewGoal(String(other.id))).locator('[data-conversation-signal]')).toHaveCount(0);
  expect((await api.get(server, '/api/chief/conversation')).conversation_id).toBe(holderConversationId);
  await api.post(server, `/api/test/connected/${holderConversationId}/message`, {
    kind: 'chief',
    id: 'chief',
    text: 'A question from your goals holder',
  });
  await expect(chief.locator('[data-conversation-signal="message"]')).toHaveCount(1, { timeout: WAIT_MS });
  await expect((await previewGoal(String(other.id))).locator('[data-conversation-signal]')).toHaveCount(0);
  await api.post(server, `/api/test/signals/${holderConversationId}/finish`, {});
  // A browser message beside either goal reuses the holder's existing conversation.
  await page.locator('[data-conversation-expand]').click({ timeout: WAIT_MS });
  await page.locator('[data-conversation-input]').fill('Help with this goal', { timeout: WAIT_MS });
  const deliveredResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/chief/conversation/send'),
    { timeout: WAIT_MS },
  );
  await page.locator('[data-conversation-input]').press('Enter', { timeout: WAIT_MS });
  const delivered = await deliveredResponse;
  await expectResponse(delivered.ok(), true, delivered);
  const conversationId = (await delivered.json()).conversation_id;
  expect(conversationId).toBe(holderConversationId);
  expect((await api.get(server, '/api/chief/conversation')).conversation_id).toBe(conversationId);
  const wire: string = (await api.get(server, `/api/test/signals/${conversationId}/delivered`)).texts.at(-1);
  expect(wire).not.toContain('You are the agent for goal ');
  expect(wire).toContain('Help with this goal');
  await api.post(server, `/api/test/signals/${conversationId}/finish`, {});
  await page.getByRole('button', { name: 'Conversation options', exact: true }).click({ timeout: WAIT_MS });
  await page.locator('[data-conversation-new-arm]').click({ timeout: WAIT_MS });
  const resetResponse = page.waitForResponse(
    (response) => response.url().endsWith('/api/chief/conversation/reset'),
    { timeout: WAIT_MS },
  );
  await page.locator('[data-conversation-new-confirm]').click({ timeout: WAIT_MS });
  const reset = await resetResponse;
  expect(reset.ok()).toBe(true);
  expect((await api.get(server, '/api/chief/conversation')).conversation_id).toBeNull();
  // Reset clears the shared pointer and preserves the holder's recorded messages.
  expect((await api.get(server, `/api/test/signals/${conversationId}/delivered`)).texts.at(-1)).toContain(
    'Help with this goal',
  );
  await page.reload();
  await page.waitForSelector('[data-goal-page]', { timeout: WAIT_MS });
  expect((await api.get(server, path)).page.blocks[0].items[0].state).toBe('done');
});
