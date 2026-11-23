/**
 * The bridge: every call a page in the app can make to the native side.
 *
 * This file is the one definition. The preload script exposes it as `window.panels`, and
 * the native side answers each call. A later change adds its call here.
 *
 * Artifact HTML runs at the app's own origin and can reach this object through `parent`.
 * Do not add a call that acts until artifacts are isolated. The address calls below
 * answer only the address page, at `panels://setup`, so an artifact gets nothing from them.
 */

export type ServerAddressResult =
  | { ok: true; address: string }
  | { ok: false; problem: string };

export interface PanelsBridge {
  /** The server address the app holds, or null before the first one. Address page only. */
  storedServerAddress(): Promise<string | null>;
  /** Check that a Panels server answers at the address, store it, and open the screens. Address page only. */
  useServerAddress(address: string): Promise<ServerAddressResult>;
}

/** Each call travels on the channel `panels:<method name>`. */
export function bridgeChannel(method: keyof PanelsBridge): string {
  return `panels:${method}`;
}
