// The native side of the Panels desktop app.
//
// It holds one server address, serves the screens from the app's own scheme, and forwards
// their requests to that server. The pages run in the sandbox with context isolation and
// no Node. They reach the native side only through the bridge in bridge.ts.
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
  handleAppScheme,
  registerAppScheme,
} from './app_scheme';
import { bridgeChannel, type ServerAddressResult } from './bridge';
import { keepLastSessionFor, lastScreenUrl, rememberLastScreen } from './last_session';
import { checkServerAddress, loadStoredServerAddress, localServerAddress, serverAddress, storeServerAddress } from './server_address';
import { rememberWindowPlace, savedWindowBounds } from './window_place';

// Tests give each run its own data folder, so runs share no stored address or window place.
if (process.env.PANELS_DESKTOP_USER_DATA) app.setPath('userData', process.env.PANELS_DESKTOP_USER_DATA);

registerAppScheme();

let mainWindow: BrowserWindow | null = null;

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
  if (maximized) window.maximize();
  window.once('ready-to-show', () => window.show());
  rememberWindowPlace(window);
  rememberLastScreen(window);
  sendLinksToTheBrowser(window);
  window.on('closed', () => {
    if (mainWindow === window) mainWindow = null;
  });
  return window;
}

function openWindow(url: string): BrowserWindow {
  mainWindow ??= createWindow();
  void mainWindow.loadURL(url);
  return mainWindow;
}

/** The last screen if the app holds a server, or one answers on this Mac. Otherwise the address page. */
async function firstPage(): Promise<string> {
  const stored = loadStoredServerAddress();
  if (stored) {
    // A switch that stopped part way still leaves the old server's session to delete.
    await keepLastSessionFor(stored).catch(() => undefined);
    return lastScreenUrl();
  }
  return (await switchServer(localServerAddress())).ok ? lastScreenUrl() : ADDRESS_PAGE_URL;
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

// The address calls answer only the address page. Artifact HTML runs at the screens'
// origin and can reach the bridge, so it must get nothing from these calls.
function fromAddressPage(event: IpcMainInvokeEvent): boolean {
  const frame = event.senderFrame;
  if (!frame || frame !== event.sender.mainFrame) return false;
  const url = new URL(frame.url);
  return url.protocol === 'panels:' && url.host === ADDRESS_PAGE_HOST;
}

function answerBridgeCalls(): void {
  ipcMain.handle(bridgeChannel('storedServerAddress'), (event) => {
    if (!fromAddressPage(event)) throw new Error('Only the address page can read the server address.');
    return serverAddress();
  });
  ipcMain.handle(bridgeChannel('useServerAddress'), async (event, typed: unknown) => {
    if (!fromAddressPage(event)) throw new Error('Only the address page can change the server address.');
    if (typeof typed !== 'string') throw new Error('The address must be text.');
    const result = await switchServer(typed);
    if (result.ok) void event.sender.loadURL(lastScreenUrl());
    return result;
  });
}

function applicationMenu(): Menu {
  const changeServer: MenuItemConstructorOptions = {
    label: 'Change Server…',
    click: () => openWindow(ADDRESS_PAGE_URL),
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
          { label: 'File', submenu: [changeServer, { type: 'separator' }, { role: 'quit' }] },
          { role: 'editMenu' },
          { role: 'viewMenu' },
          { role: 'windowMenu' },
        ];
  return Menu.buildFromTemplate(template);
}

void app.whenReady().then(async () => {
  handleAppScheme();
  answerBridgeCalls();
  Menu.setApplicationMenu(applicationMenu());
  openWindow(await firstPage());
  // A Mac app stays open with no window. A click on its Dock icon brings the window back.
  app.on('activate', () => {
    if (!mainWindow) openWindow(serverAddress() ? lastScreenUrl() : ADDRESS_PAGE_URL);
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
