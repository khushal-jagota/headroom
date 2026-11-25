/**
 * The bridge: every call a page in the app can make to the native side.
 *
 * This file is the one definition. The preload script exposes it as `window.panels`, and
 * the native side answers each call. A later change adds its call here.
 *
 * Artifact HTML runs at the app's own origin and can reach this object through `parent`.
 * Calls that act answer only their separate setup or recovery origin, never the app origin.
 * An artifact can call parent.panels as the top app frame. Only the address read is
 * available there. Server changes answer `panels://setup` alone. Screen retry answers
 * `panels://recovery` alone.
 */

export type ServerAddressResult =
  | { ok: true; address: string }
  | { ok: false; problem: string };

export interface PanelsBridge {
  /** Native recovery page only: retry the rejected screen copy. */
  retryScreens(): Promise<boolean>;
  /** Setup only: the default local address. Does not connect. */
  localServerAddress(): Promise<string>;
  /** Setup only: cancel a change and return to the current server. */
  returnToServer(): Promise<void>;
  /** The server address the app holds, or null before the first one. Read-only in the app and setup. */
  storedServerAddress(): Promise<string | null>;
  /** Check that a Panels server answers at the address, store it, and open the screens. Address page only. */
  useServerAddress(address: string): Promise<ServerAddressResult>;
}

/** The native window's state, which the main process sends to the page as it changes. */
export interface DesktopWindowState {
  focused: boolean;
  fullScreen: boolean;
}

/** The channel that carries `DesktopWindowState` from the main process to the page. */
export const WINDOW_STATE_CHANNEL = 'panels:window-state';

/** Each call travels on the channel `panels:<method name>`. */
export function bridgeChannel(method: keyof PanelsBridge): string {
  return `panels:${method}`;
}
