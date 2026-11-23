// What the app keeps of the last session: the screen the window was on, kept here, and
// the screens' saved data, which the screens keep in their own IndexedDB. Both belong to
// one server, which is kept with the screen.
import { session, type BrowserWindow } from 'electron';
import { SCREENS_HOST, SCREENS_URL } from './app_scheme';
import { readJson, writeJson } from './stored_json';

type LastSession = { server: string; hash: string };

// Read on first use: the data folder can change at start, before this is needed.
let last: LastSession | null = null;

function lastSession(): LastSession {
  last ??= readJson<LastSession>('last_session.json') ?? { server: '', hash: '' };
  return last;
}

/** The screens, at the place the person last left them. */
export function lastScreenUrl(): string {
  return `${SCREENS_URL}${lastSession().hash}`;
}

/** Follow the screens' address, which changes in the hash, and save it when the window closes. */
export function rememberLastScreen(window: BrowserWindow): void {
  const follow = (url: string) => {
    const address = new URL(url);
    if (address.protocol === 'panels:' && address.host === SCREENS_HOST) lastSession().hash = address.hash;
  };
  window.webContents.on('did-navigate', (_event, url) => follow(url));
  window.webContents.on('did-navigate-in-page', (_event, url) => follow(url));
  window.on('close', () => {
    if (last) writeJson('last_session.json', last);
  });
}

/**
 * Make the last session belong to this server. Another server has other Tickets, so a
 * session kept for a different one is deleted first: the screens' saved data and the last
 * screen. If the delete fails, this throws, and the session still names the old server.
 */
export async function keepLastSessionFor(server: string): Promise<void> {
  if (lastSession().server === server) return;
  await session.defaultSession.clearStorageData({ origin: `panels://${SCREENS_HOST}`, storages: ['indexdb'] });
  last = { server, hash: '' };
  writeJson('last_session.json', last);
}
