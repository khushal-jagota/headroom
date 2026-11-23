// The native side of the Panels desktop app.
//
// It holds one server address, serves the screens from the app's own scheme, and forwards
// their requests to that server. It also shows the server's notifications through macOS.
// The pages run in the sandbox with context isolation and no Node. They reach the native
// side only through the bridge in bridge.ts.
import {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  shell,
  type IpcMainInvokeEvent,
  type MenuItemConstructorOptions,
} from 'electron';
import { join } from 'node:path';
import {
  ADDRESS_PAGE_HOST,
  ADDRESS_PAGE_URL,
  SCREENS_HOST,
  SCREENS_URL,
  handleAppScheme,
  PACKED,
  forward,
  selectScreenPresentation,
  registerAppScheme,
} from './app_scheme';
import { bridgeChannel, type ServerAddressResult } from './bridge';
import { keepLastSessionFor, lastScreenUrl, rememberLastScreen } from './last_session';
import { holdNotificationStream } from './notifications';
import { checkServerAddress, loadStoredServerAddress, localServerAddress, serverAddress, storeServerAddress } from './server_address';
import { rememberWindowPlace, savedWindowBounds } from './window_place';
import { startShellUpdates } from './updates';
import type { ShellUpdates } from './updates/contracts';
import { DESKTOP_BRIDGE_VERSION } from './bridge_version';
import { ScreenStore } from './screens/store';
import { ScreenController } from './screens/controller';
import { SCREEN_CHECK_INTERVAL_MS, SCREEN_READY_POLL_MS, SCREEN_START_TIMEOUT_MS } from './screens/configuration';

// Tests give each run its own data folder, so runs share no stored address or window place.
if (process.env.PANELS_DESKTOP_USER_DATA) app.setPath('userData', process.env.PANELS_DESKTOP_USER_DATA);

registerAppScheme();

let mainWindow: BrowserWindow | null = null;
let startingFirstWindow = true;
let preparingScreens = 0;
let shellUpdates: ShellUpdates | undefined;
let screens: ScreenController;
let screenInterval: ReturnType<typeof setInterval> | undefined;

function createWindow(): BrowserWindow {
  const { bounds, maximized } = savedWindowBounds();
  const window = new BrowserWindow({
    ...bounds,
    title: 'Panels',
    backgroundColor: '#11151f',
    show: false,
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  shellUpdates?.windowChanged();
  if (maximized) window.maximize();
  window.once('ready-to-show', () => window.show());
  rememberWindowPlace(window);
  rememberLastScreen(window);
  sendLinksToTheBrowser(window);
  observeScreenStart(window);
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
    shellUpdates?.windowChanged();
  });
  return window;
}

function openWindow(url: string): BrowserWindow {
  mainWindow ??= createWindow();
  void mainWindow.loadURL(url);
  return mainWindow;
}

/** Prepare the current server copy before a notification or Dock navigation. */
async function openScreensWindow(url: string, reconnect = false): Promise<BrowserWindow | null> {
  mainWindow ??= createWindow();
  const window = mainWindow;
  preparingScreens++;
  try {
    if (reconnect) await screens.connect();
    else await screens.prepare();
    if (window.isDestroyed()) return null;
    void window.loadURL(url);
    return window;
  } finally { preparingScreens--; }
}

/** A click on a notification opens its link in the screens, and brings the window forward. */
async function openRoute(route: string): Promise<void> {
  const url = new URL(route, SCREENS_URL);
  if (url.protocol !== 'panels:' || url.host !== SCREENS_HOST) return;
  const window = await openScreensWindow(url.href);
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.focus();
  app.focus({ steal: true });
}

/** The last screen if the app holds a server. Otherwise ask where the server lives. */
async function firstPage(): Promise<string> {
  const stored = loadStoredServerAddress();
  if (stored) {
    // A switch that stopped part way still leaves the old server's session to delete.
    await keepLastSessionFor(stored).catch(() => undefined);
    return lastScreenUrl();
  }
  return ADDRESS_PAGE_URL;
}

/** Use a typed address. A different server's session is deleted before the address is stored. */
async function switchServer(typed: string): Promise<ServerAddressResult> {
  const result = await checkServerAddress(typed);
  if (!result.ok) return result;
  try {
    await keepLastSessionFor(result.address);
  } catch {
    return { ok: false, problem: 'The app could not clear the data it kept for the last server.' };
  }
  storeServerAddress(result.address);
  shellUpdates?.serverChanged();
  return result;
}

// A link or a new window that leaves the app opens in the default browser. A new window
// for a server path, such as a file opened in another tab, opens at the server's address.
function sendLinksToTheBrowser(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    openOutside(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (new URL(url).protocol === 'panels:') return;
    event.preventDefault();
    openOutside(url);
  });
}

function openOutside(link: string): void {
  const url = new URL(link);
  if (['http:', 'https:', 'mailto:'].includes(url.protocol)) {
    void shell.openExternal(url.href);
    return;
  }
  const address = serverAddress();
  if (url.protocol === 'panels:' && url.host === SCREENS_HOST && address)
    void shell.openExternal(`${address}${url.pathname}${url.search}${url.hash}`);
}

// Calls that act answer only the separate setup origin. An artifact can invoke the
// parent's bridge as the top app frame, so a frame check alone is not sufficient.
function fromAddressPage(event: IpcMainInvokeEvent): boolean {
  const frame = event.senderFrame;
  if (!frame || frame !== event.sender.mainFrame) return false;
  const url = new URL(frame.url);
  return url.protocol === 'panels:' && url.host === ADDRESS_PAGE_HOST;
}

function answerBridgeCalls(): void {
  ipcMain.handle(bridgeChannel('localServerAddress'), (event) => {
    if (!fromAddressPage(event)) throw new Error('Only setup can read the local address.');
    return localServerAddress();
  });
  ipcMain.handle(bridgeChannel('returnToServer'), (event) => {
    if (!fromAddressPage(event)) throw new Error('Only setup can return to the server.');
    if (serverAddress()) void event.sender.loadURL(lastScreenUrl());
  });
  ipcMain.handle(bridgeChannel('storedServerAddress'), (event) => {
    const frame = event.senderFrame;
    const url = frame ? new URL(frame.url) : null;
    if (frame !== event.sender.mainFrame || url?.protocol !== 'panels:' || ![ADDRESS_PAGE_HOST, SCREENS_HOST].includes(url.host))
      throw new Error('Only the app can read the server address.');
    return serverAddress();
  });
  ipcMain.handle(bridgeChannel('useServerAddress'), async (event, typed: unknown) => {
    if (!fromAddressPage(event)) throw new Error('Only the address page can change the server address.');
    if (typeof typed !== 'string') throw new Error('The address must be text.');
    const result = await switchServer(typed);
    if (result.ok) {
      holdNotificationStream(openRoute);
      await openScreensWindow(lastScreenUrl(), true);
    }
    return result;
  });
}

/** A passive marker proves that the main document mounted the screens, not just HTML. */
function isScreensDocument(address: string): boolean {
  if (!address) return false;
  try {
    const url = new URL(address);
    return url.protocol === 'panels:' && url.host === SCREENS_HOST && (url.pathname === '/' || url.pathname === '/index.html');
  } catch { return false; }
}

function observeScreenStart(window: BrowserWindow): void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  let navigation = 0;
  const clear = () => {
    if (timer) clearTimeout(timer);
    if (watchdog) clearTimeout(watchdog);
    timer = undefined;
    watchdog = undefined;
    navigation++;
  };
  window.webContents.on('did-start-navigation', (_event, url, inPlace, mainFrame) => {
    if (!mainFrame || inPlace) return;
    clear();
    if (!screens?.store.pending || !isScreensDocument(url)) return;
    const token = navigation;
    watchdog = setTimeout(() => {
      if (token === navigation && screens.store.pending && !window.isDestroyed()) {
        clear();
        screens.failed();
      }
    }, SCREEN_START_TIMEOUT_MS);
  });
  window.webContents.on('did-finish-load', () => {
    if (!screens?.store.pending || !isScreensDocument(window.webContents.getURL())) return;
    const token = navigation;
    const poll = async (): Promise<void> => {
      if (window.isDestroyed() || token !== navigation || !screens.store.pending) return;
      try {
        const ready = await window.webContents.executeJavaScript('document.documentElement.dataset.panelsReady === "true"');
        if (token !== navigation) return;
        if (ready) { clear(); screens.ready(); return; }
      } catch { /* A navigation can replace this document during the read. */ }
      if (token === navigation) timer = setTimeout(() => { void poll(); }, SCREEN_READY_POLL_MS);
    };
    void poll();
  });
  window.webContents.on('did-fail-load', (_event, code, _description, url, mainFrame) => {
    if (mainFrame && code !== -3) { clear(); if (isScreensDocument(url)) screens?.failed(); }
  });
  window.webContents.on('render-process-gone', () => { clear(); if (isScreensDocument(window.webContents.getURL())) screens?.failed(); });
  window.on('closed', clear);
}

function applicationMenu(): Menu {
  const changeServer: MenuItemConstructorOptions = {
    label: 'Change Server…',
    click: () => openWindow(ADDRESS_PAGE_URL),
  };
  const retryScreens: MenuItemConstructorOptions = {
    label: 'Try Screens Again',
    click: () => { void screens.retry(); },
  };
  const template: MenuItemConstructorOptions[] =
    process.platform === 'darwin'
      ? [
          {
            label: app.name,
            submenu: [
              { role: 'about' },
              { type: 'separator' },
              changeServer,
              retryScreens,
              { type: 'separator' },
              { role: 'services' },
              { type: 'separator' },
              { role: 'hide' },
              { role: 'hideOthers' },
              { role: 'unhide' },
              { type: 'separator' },
              { role: 'quit' },
            ],
          },
          { role: 'fileMenu' },
          { role: 'editMenu' },
          { role: 'viewMenu' },
          { role: 'windowMenu' },
        ]
      : [
          { label: 'File', submenu: [changeServer, retryScreens, { type: 'separator' }, { role: 'quit' }] },
          { role: 'editMenu' },
          { role: 'viewMenu' },
          { role: 'windowMenu' },
        ];
  return Menu.buildFromTemplate(template);
}

void app.whenReady().then(async () => {
  handleAppScheme();
  if (app.isPackaged && process.platform === 'darwin') shellUpdates = startShellUpdates(serverAddress);
  screens = new ScreenController(new ScreenStore(join(app.getPath('userData'), 'screens'), PACKED), {
    address: serverAddress,
    canActivate: () => startingFirstWindow || preparingScreens > 0 || (mainWindow !== null && isScreensDocument(mainWindow.webContents.getURL())),
    request: (path, signal) => {
      const url = new URL(path, SCREENS_URL);
      return forward(new Request(url, { signal }), url);
    },
    requireBridgeVersion: (required) => shellUpdates?.requireBridgeVersion(required) ?? Promise.resolve(required <= DESKTOP_BRIDGE_VERSION),
    present: (value, reload) => {
      selectScreenPresentation(value);
      if (reload && mainWindow && new URL(mainWindow.webContents.getURL() || ADDRESS_PAGE_URL).host === SCREENS_HOST) {
        void mainWindow.loadURL(lastScreenUrl());
      }
    },
  });
  answerBridgeCalls();
  Menu.setApplicationMenu(applicationMenu());
  const first = await firstPage();
  await screens.connect();
  openWindow(first);
  startingFirstWindow = false;
  screenInterval = setInterval(() => { void screens.check(); }, SCREEN_CHECK_INTERVAL_MS);
  screenInterval.unref();
  holdNotificationStream(openRoute);
  // A Mac app stays open with no window. A click on its Dock icon brings the window back.
  app.on('activate', () => {
    if (!mainWindow) {
      if (serverAddress()) void openScreensWindow(lastScreenUrl());
      else openWindow(ADDRESS_PAGE_URL);
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (screenInterval) clearInterval(screenInterval);
  screens?.stop();
  shellUpdates?.stop();
});
