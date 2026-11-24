// The preload script. It runs in the sandbox, where `require` reaches only Electron's
// own modules, so it imports nothing from this package but types.
import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopWindowState, PanelsBridge } from './bridge';

// The same name as `WINDOW_STATE_CHANNEL` in bridge.ts, which the sandbox cannot load.
const WINDOW_STATE_CHANNEL = 'panels:window-state';

// The same channel rule as `bridgeChannel` in bridge.ts, which the sandbox cannot load.
function call<Method extends keyof PanelsBridge>(
  method: Method,
  ...args: Parameters<PanelsBridge[Method]>
): ReturnType<PanelsBridge[Method]> {
  return ipcRenderer.invoke(`panels:${method}`, ...args) as ReturnType<PanelsBridge[Method]>;
}

const bridge: PanelsBridge = {
  localServerAddress: () => call('localServerAddress'),
  returnToServer: () => call('returnToServer'),
  storedServerAddress: () => call('storedServerAddress'),
  useServerAddress: (address) => call('useServerAddress', address),
};

contextBridge.exposeInMainWorld('panels', bridge);

// The window's own state, as attributes on the page root. The page styles the top row from
// them: `data-desktop-window` marks the desktop window, `data-window-inactive` dims it while
// another app has focus, and `data-window-full-screen` drops the space for the traffic lights.
// The native window sends the state, because a page focus check also fails when focus moves
// into an artifact frame. The preload runs only in the main frame.
function markWindowState(state: DesktopWindowState): void {
  const root = document.documentElement;
  root.toggleAttribute('data-window-inactive', !state.focused);
  root.toggleAttribute('data-window-full-screen', state.fullScreen);
}

function markDesktopWindow(): void {
  document.documentElement.toggleAttribute('data-desktop-window', true);
}

// Only macOS hides the system title bar, so only there does the page draw its own top row.
if (process.platform === 'darwin') {
  if (document.documentElement) markDesktopWindow();
  else document.addEventListener('readystatechange', markDesktopWindow, { once: true });
}
ipcRenderer.on(WINDOW_STATE_CHANNEL, (_event, state: DesktopWindowState) => markWindowState(state));
