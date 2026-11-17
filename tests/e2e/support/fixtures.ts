/**
 * Browser test fixtures. Import `test` and `expect` from here, not from @playwright/test.
 *
 * Each server fixture starts a fresh isolated server for the test and stops it after.
 * `server` runs the production entry, `server/main.ts`. `railServer` and `signalServer`
 * run `server/test_host.ts`, which adds scripted backends and the /api/test routes; they
 * differ only in their database file name. `newContext` makes browser contexts that keep
 * a trace, screenshots and page HTML when the test fails.
 */
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { test as base, expect, type BrowserContext, type BrowserContextOptions, type Page } from '@playwright/test';
import {
  WAIT_MS,
  serverLogTail,
  startServer,
  stopServer,
  type ServerHandle,
  type ServerOptions,
} from './server.ts';

export { expect };
export * from './server.ts';
export * from './api.ts';
export * from './agents.ts';

interface Fixtures {
  /** A fresh `server/main.ts`. */
  server: ServerHandle;
  /** A fresh `server/test_host.ts` on `rail-proof.db`. */
  railServer: ServerHandle;
  /** A fresh `server/test_host.ts` on `conversation-signals-proof.db`. */
  signalServer: ServerHandle;
  /** Start more servers, or servers with special options. All stop after the test. */
  serverFactory: (options?: ServerOptions) => Promise<ServerHandle>;
  newContext: (options?: BrowserContextOptions) => Promise<BrowserContext>;
}

export const test = base.extend<Fixtures>({
  serverFactory: async ({}, use, testInfo) => {
    const handles: ServerHandle[] = [];
    await use(async (options) => {
      const handle = await startServer(options);
      handles.push(handle);
      return handle;
    });
    for (const [index, handle] of handles.entries()) {
      await stopServer(handle);
      if (testInfo.status !== testInfo.expectedStatus)
        await testInfo.attach(`server-${index}.log`, { body: serverLogTail(handle, 400), contentType: 'text/plain' });
      rmSync(handle.root, { recursive: true, force: true });
    }
  },
  server: async ({ serverFactory }, use) => use(await serverFactory()),
  railServer: async ({ serverFactory }, use) =>
    use(await serverFactory({ host: 'test_host', databaseName: 'rail-proof.db' })),
  signalServer: async ({ serverFactory }, use) =>
    use(await serverFactory({ host: 'test_host', databaseName: 'conversation-signals-proof.db' })),
  newContext: async ({ browser }, use, testInfo) => {
    const contexts: BrowserContext[] = [];
    await use(async (options) => {
      const context = await browser.newContext(options);
      await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
      contexts.push(context);
      return context;
    });
    const failed = testInfo.status !== testInfo.expectedStatus;
    for (const [index, context] of contexts.entries()) {
      if (failed) {
        const folder = testInfo.outputPath('browser-failure');
        mkdirSync(folder, { recursive: true });
        for (const [pageIndex, page] of context.pages().entries()) {
          const stem = join(folder, `context-${index}-page-${pageIndex}`);
          try {
            await page.screenshot({ path: `${stem}.png`, fullPage: true, timeout: 5_000 });
            writeFileSync(`${stem}.html`, await page.content());
          } catch (error) {
            writeFileSync(`${stem}.capture-error.txt`, String(error));
          }
        }
        await context.tracing.stop({ path: join(folder, `context-${index}-trace.zip`) });
      } else {
        await context.tracing.stop();
      }
      // A route handler can still await route.fetch() after the last assertion.
      if (context.pages().length) await context.unrouteAll({ behavior: 'wait' });
      await context.close();
    }
  },
});

/**
 * Open a page and wait until the change stream's first reconciliation has finished, so
 * the ready selector comes from the reconciled page rather than the first read.
 */
export async function openPage(
  context: BrowserContext,
  server: ServerHandle,
  route: string,
  readySelector: string,
): Promise<Page> {
  const page = await context.newPage();
  await page.goto(`${server.base}/${route}`);
  await page.waitForSelector(readySelector, { timeout: WAIT_MS });
  await page.waitForFunction(
    () => {
      const debug = (window as unknown as { __plannerDebug?: { sseReconciliations: number } }).__plannerDebug;
      return debug !== undefined && debug.sseReconciliations >= 1;
    },
    undefined,
    { timeout: WAIT_MS },
  );
  await page.waitForSelector(readySelector, { timeout: WAIT_MS });
  return page;
}

/** Open the Workspace status group named by `key`. */
export async function openStatusGroup(page: Page, key: string): Promise<void> {
  const group = `[data-bucket-key="${key}"]`;
  await page.waitForSelector(group, { timeout: WAIT_MS });
  if ((await page.getAttribute(group, 'open')) === null) await page.click(`${group} > summary`);
  await page.waitForFunction(
    (selector) => (document.querySelector(selector) as HTMLDetailsElement | null)?.open === true,
    group,
    { timeout: WAIT_MS },
  );
}

/** Capture a review image only when PANELS_UI_PROOF_DIR asks for one. */
export async function captureSuccess(page: Page, name: string): Promise<void> {
  const output = process.env.PANELS_UI_PROOF_DIR;
  if (!output) return;
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, name.split('/').pop()!), fullPage: true, animations: 'disabled' });
}

/**
 * Python's `assert actual == expected, response.text()`: the body is read only when the
 * check fails. A browser response body can be gone once the page moves on, so reading it
 * on success would fail a passing check.
 */
export async function expectResponse<T>(
  actual: T,
  expected: T,
  response: { text(): Promise<string> },
): Promise<void> {
  if (Object.is(actual, expected)) return;
  expect(actual, await response.text().catch((error: unknown) => String(error))).toBe(expected);
}
