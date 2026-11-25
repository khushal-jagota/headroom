// The native side of the Panels desktop app.
//
// It holds one server address, serves the screens from the app's own scheme, and forwards
// their requests to that server. It also shows the server's notifications through macOS.
// The pages run in the sandbox with context isolation and no Node. They reach the native
// side only through the bridge in bridge.ts.
import {
  app,
  BrowserWindow,
  dialog,
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
  SCREEN_RECOVERY_HOST,
  SCREEN_RECOVERY_URL,
  SCREENS_URL,
  handleAppScheme,
  PACKED,
  forward,
  selectScreenPresentation,
  registerAppScheme,
} from './app_scheme';
import { bridgeChannel, WINDOW_STATE_CHANNEL, type DesktopWindowState, type ServerAddressResult } from './bridge';
import { keepLastSessionFor, lastScreenUrl, rememberLastScreen } from './last_session';
import { holdNotificationStream } from './notifications';
import { checkServerAddress, loadStoredServerAddress, localServerAddress, serverAddress, storeServerAddress } from './server_address';
import { rememberWindowPlace, savedWindowBounds } from './window_place';
import { startShellUpdates } from './updates';
import { prepareApplicationLocation } from './application_location';
import type { ShellUpdates } from './updates/contracts';
import { DESKTOP_BRIDGE_VERSION } from './bridge_version';
import { ScreenStore } from './screens/store';
import { ScreenController } from './screens/controller';
import { SCREEN_CHECK_INTERVAL_MS, SCREEN_READY_POLL_MS, SCREEN_START_TIMEOUT_MS } from './screens/configuration';

// Tests give each run its own data folder, so runs share no stored address or window place.
if (process.env.PANELS_DESKTOP_USER_DATA) app.setPath('userData', process.env.PANELS_DESKTOP_USER_DATA);

registerAppScheme();

// Electron places the lights' top-left corner. This puts them where a native 52px toolbar puts
// them, measured on macOS 27: the first light's left edge at x 19 and their centres at y 26.
const TRAFFIC_LIGHT_POSITION = { x: 19, y: 19 };

let mainWindow: BrowserWindow | null = null;
let startingFirstWindow = true;
let preparingScreens = 0;
let shellUpdates: ShellUpdates | undefined;
let screens: ScreenController;
let screenRecovery = false;
let screenInterval: ReturnType<typeof setInterval> | undefined;

function createWindow(): BrowserWindow {
  const { bounds, maximized } = savedWindowBounds();
  const window = new BrowserWindow({
    ...bounds,
    title: 'Panels',
    // The page colour (`--surface-1`), so the window does not flash another colour on load.
    backgroundColor: '#121212',
    // On macOS, no system title bar: the traffic lights sit centred on the app's own 52px top
    // row. Other systems keep their own frame, and the page draws no top row there.
    ...(process.platform === 'darwin' ? { titleBarStyle: 'hiddenInset' as const, trafficLightPosition: TRAFFIC_LIGHT_POSITION } : {}),
    // At 720px or less the screens change to the phone layout, which the window never shows.
    minWidth: 900,
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
  sendWindowState(window);
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
    shellUpdates?.windowChanged();
  });
  return window;
}

/** The page dims the top row while another app has focus and drops the lights' space in full screen. */
function sendWindowState(window: BrowserWindow): void {
  const send = () => {
    if (window.isDestroyed()) return;
    const state: DesktopWindowState = { focused: window.isFocused(), fullScreen: window.isFullScreen() };
    window.webContents.send(WINDOW_STATE_CHANNEL, state);
  };
  window.on('focus', send);
  window.on('blur', send);
  window.on('enter-full-screen', send);
  window.on('leave-full-screen', send);
  window.webContents.on('dom-ready', send);
}

function openWindow(url: string): BrowserWindow {
  mainWindow ??= createWindow();
  void mainWindow.loadURL(screenRecovery && new URL(url).host === SCREENS_HOST ? SCREEN_RECOVERY_URL : url);
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
    void window.loadURL(screenRecovery ? SCREEN_RECOVERY_URL : url);
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

// Every link the page follows or opens in a new window passes here, and each ends in one of
// three places: the default browser, a page of the app, or a message that says why not.
function sendLinksToTheBrowser(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    openLink(window, url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (new URL(url).protocol === 'panels:') return;
    event.preventDefault();
    openLink(window, url);
  });
}

function openLink(window: BrowserWindow, link: string): void {
  const url = parseLink(link);
  if (!url) return void refuseLink(window, link, 'It is not a valid address.');
  const address = serverAddress();
  const ownServer = address !== null && url.protocol !== 'mailto:' && url.origin === new URL(address).origin;
  // A page of the connected server opens in the app. The server's data and files stay with
  // the browser, which can show or download them.
  if (ownServer && !/^\/(api|files)(\/|$)/.test(url.pathname)) {
    if (!window.isDestroyed()) void window.loadURL(`${SCREENS_URL.slice(0, -1)}${url.pathname}${url.search}${url.hash}`);
    return;
  }
  if (['http:', 'https:', 'mailto:'].includes(url.protocol)) {
    shell.openExternal(url.href).catch((error: Error) => refuseLink(window, link, `The browser would not open it: ${error.message}`));
    return;
  }
  // A link to a server path in a new window, such as a file opened in another tab, opens at the server's address.
  if (url.protocol === 'panels:' && url.host === SCREENS_HOST && address) {
    shell.openExternal(`${address}${url.pathname}${url.search}${url.hash}`).catch((error: Error) => refuseLink(window, link, `The browser would not open it: ${error.message}`));
    return;
  }
  refuseLink(window, link, 'Panels opens web and email links only.');
}

function parseLink(link: string): URL | null {
  try { return new URL(link); } catch { return null; }
}

/** The click had no result the person could see, so say why. */
function refuseLink(window: BrowserWindow, link: string, reason: string): void {
  if (window.isDestroyed()) return;
  void dialog.showMessageBox(window, { type: 'info', message: 'Panels could not open this link.', detail: `${link}\n\n${reason}` });
}

// Setup actions answer only the setup origin. Recovery retry has its own origin below.
// An artifact can invoke the
// parent's bridge as the top app frame, so a frame check alone is not sufficient.
function fromAddressPage(event: IpcMainInvokeEvent): boolean {
  const frame = event.senderFrame;
  if (!frame || frame !== event.sender.mainFrame) return false;
  const url = new URL(frame.url);
  return url.protocol === 'panels:' && url.host === ADDRESS_PAGE_HOST;
}

function answerBridgeCalls(): void {
  ipcMain.handle(bridgeChannel('retryScreens'), async (event) => {
    const frame = event.senderFrame;
    if (!frame || frame !== event.sender.mainFrame || new URL(frame.url).host !== SCREEN_RECOVERY_HOST
      || new URL(frame.url).protocol !== 'panels:') throw new Error('Only recovery can retry screens.');
    await screens.retry();
    return !screens.recovery;
  });
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
  if (!await prepareApplicationLocation()) return;
  handleAppScheme();
  if (app.isPackaged && process.platform === 'darwin') shellUpdates = startShellUpdates(serverAddress);
  screens = new ScreenController(new ScreenStore(join(app.getPath('userData'), 'screens'), PACKED), {
    address: serverAddress,
    canActivate: () => startingFirstWindow || preparingScreens > 0 || (mainWindow !== null && (isScreensDocument(mainWindow.webContents.getURL()) || new URL(mainWindow.webContents.getURL() || ADDRESS_PAGE_URL).host === SCREEN_RECOVERY_HOST)),
    prepareActivation: async () => {
      const window = mainWindow;
      if (!window || !isScreensDocument(window.webContents.getURL())) return true;
      try {
        return await window.webContents.executeJavaScript('window.__panelsPrepareScreenReplacement?.()') !== false;
      } catch (error) {
        console.warn('Desktop screen handoff:', error);
        return false;
      }
    },
    cancelActivation: async () => {
      const window = mainWindow;
      if (!window || !isScreensDocument(window.webContents.getURL())) return;
      try {
        await window.webContents.executeJavaScript('window.__panelsCancelScreenReplacement?.()');
      } catch (error) { console.warn('Desktop screen handoff cancellation:', error); }
    },
    request: (path, signal) => {
      const url = new URL(path, SCREENS_URL);
      return forward(new Request(url, { signal }), url);
    },
    requireBridgeVersion: (required) => shellUpdates?.requireBridgeVersion(required) ?? Promise.resolve(required <= DESKTOP_BRIDGE_VERSION),
    present: (value, reload) => {
      selectScreenPresentation(value);
      screenRecovery = value.screenRecovery;
      if (!reload || !mainWindow) return;
      const window = mainWindow;
      const host = new URL(window.webContents.getURL() || ADDRESS_PAGE_URL).host;
      if (![SCREENS_HOST, SCREEN_RECOVERY_HOST].includes(host)) return;
      if (screenRecovery) {
        // Recovery belongs to the native shell and survives a failed renderer.
        void window.loadURL(SCREEN_RECOVERY_URL);
      } else if (host === SCREEN_RECOVERY_HOST) {
        void window.loadURL(lastScreenUrl());
      } else {
        // The route lives in the hash. A same-URL load would not replace the document.
        window.webContents.reload();
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
