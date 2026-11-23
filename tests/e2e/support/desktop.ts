/**
 * Launch the built desktop app (`desktop/build`) under Playwright's Electron driver.
 *
 * Each launch gets its own data folder, so runs share no stored server address or window
 * place. The app's outside opens are recorded instead of reaching a browser.
 */
import { mkdtempSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { _electron, type ElectronApplication, type Page } from '@playwright/test';
import { REPO_ROOT, WAIT_MS } from './server.ts';

const DESKTOP = join(REPO_ROOT, 'desktop');

export interface DesktopApp {
  app: ElectronApplication;
  window: Page;
  userData: string;
  /** Every URL the app handed to the default browser. */
  openedOutside(): Promise<string[]>;
}

export interface DesktopLaunch {
  /** The address the app tries first, instead of the default local port. */
  localServerAddress: string;
  /** Reuse the data folder of an earlier launch. */
  userData?: string;
}

export function newDesktopUserData(): string {
  return mkdtempSync(join(tmpdir(), 'panels-desktop-'));
}

export async function launchDesktopApp(launch: DesktopLaunch): Promise<DesktopApp> {
  const userData = launch.userData ?? newDesktopUserData();
  const executablePath = createRequire(join(DESKTOP, 'package.json'))('electron') as string;
  const app = await _electron.launch({
    executablePath,
    // The VPS and CI runners have no setuid sandbox helper. The page keeps Electron's
    // own sandbox, context isolation and no Node, which the app sets per window.
    args: [DESKTOP, '--no-sandbox'],
    env: {
      ...process.env,
      PANELS_DESKTOP_USER_DATA: userData,
      PANELS_LOCAL_SERVER_ADDRESS: launch.localServerAddress,
    },
    timeout: WAIT_MS * 3,
  });
  await app.evaluate(({ shell }) => {
    const opened: string[] = [];
    (globalThis as { openedOutside?: string[] }).openedOutside = opened;
    shell.openExternal = async (url: string) => {
      opened.push(url);
    };
  });
  const window = await app.firstWindow();
  return {
    app,
    window,
    userData,
    openedOutside: () => app.evaluate(() => [...((globalThis as { openedOutside?: string[] }).openedOutside ?? [])]),
  };
}

/** Wait until the screens are up and their change stream has reconciled once. */
export async function waitForScreens(window: Page, readySelector: string): Promise<void> {
  await window.waitForSelector(readySelector, { timeout: WAIT_MS * 2 });
  await window.waitForFunction(
    () => {
      const debug = (window as unknown as { __plannerDebug?: { sseReconciliations: number } }).__plannerDebug;
      return debug !== undefined && debug.sseReconciliations >= 1;
    },
    undefined,
    { timeout: WAIT_MS },
  );
}
