// The window opens where the person last left it, at the size they left it.
import { screen, type BrowserWindow, type Rectangle } from 'electron';
import { readJson, writeJson } from './stored_json';

type WindowPlace = { bounds: Rectangle; maximized: boolean };

const DEFAULT_SIZE = { width: 1280, height: 860 };

/** The bounds to open with. A place on a display that is gone falls back to the default size. */
export function savedWindowBounds(): { bounds: Partial<Rectangle>; maximized: boolean } {
  const saved = readJson<WindowPlace>('window.json');
  if (saved && onSomeDisplay(saved.bounds)) return saved;
  return { bounds: DEFAULT_SIZE, maximized: false };
}

/** Save the place whenever the window closes, which also happens when the app quits. */
export function rememberWindowPlace(window: BrowserWindow): void {
  window.on('close', () => {
    writeJson('window.json', {
      bounds: window.getNormalBounds(),
      maximized: window.isMaximized(),
    } satisfies WindowPlace);
  });
}

function onSomeDisplay(bounds: Rectangle): boolean {
  return screen.getAllDisplays().some(({ workArea }) => {
    const width = Math.min(bounds.x + bounds.width, workArea.x + workArea.width) - Math.max(bounds.x, workArea.x);
    const height = Math.min(bounds.y + bounds.height, workArea.y + workArea.height) - Math.max(bounds.y, workArea.y);
    return width >= 100 && height >= 100;
  });
}
