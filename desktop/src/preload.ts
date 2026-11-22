// The preload script. It runs in the sandbox, where `require` reaches only Electron's
// own modules, so it imports nothing from this package but types.
import { contextBridge, ipcRenderer } from 'electron';
import type { PanelsBridge } from './bridge';

// The same channel rule as `bridgeChannel` in bridge.ts, which the sandbox cannot load.
function call<Method extends keyof PanelsBridge>(
  method: Method,
  ...args: Parameters<PanelsBridge[Method]>
): ReturnType<PanelsBridge[Method]> {
  return ipcRenderer.invoke(`panels:${method}`, ...args) as ReturnType<PanelsBridge[Method]>;
}

const bridge: PanelsBridge = {
  storedServerAddress: () => call('storedServerAddress'),
  useServerAddress: (address) => call('useServerAddress', address),
};

contextBridge.exposeInMainWorld('panels', bridge);
