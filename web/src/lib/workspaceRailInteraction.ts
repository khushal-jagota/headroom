// Adjustable values from the approved left-panel design.
export const RAIL_MIN_WIDTH = 232;
export const RAIL_MAX_WIDTH = 520;
export const RAIL_DEFAULT_WIDTH = "clamp(17rem, 22vw, 20rem)";
// The expanded Settled section includes its header in this share of usable rail height.
export const RAIL_SETTLED_MAX_HEIGHT_SHARE = 0.5;
export const RAIL_WIDTH_STORAGE_KEY = "panels.workspace.rail-width";
export const RAIL_HOVER_ENTER_MS = 200;
export const RAIL_HOVER_EXIT_MS = 90;
export const RAIL_KEYBOARD_STEP = 8;
export const RAIL_KEYBOARD_LARGE_STEP = 24;

type PreferenceStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function clampRailWidth(width: number): number {
  return Math.round(Math.max(RAIL_MIN_WIDTH, Math.min(RAIL_MAX_WIDTH,
    Number.isFinite(width) ? width : RAIL_MIN_WIDTH)));
}

/** A null preference uses the responsive CSS default. Storage can be unavailable. */
export function readRailWidth(storage: PreferenceStorage | null): number | null {
  try {
    const value = storage?.getItem(RAIL_WIDTH_STORAGE_KEY);
    if (!value?.trim()) return null;
    const width = Number(value);
    return Number.isFinite(width) ? clampRailWidth(width) : null;
  } catch { return null; }
}

export function saveRailWidth(storage: PreferenceStorage | null, width: number | null): void {
  try {
    if (width === null) storage?.removeItem(RAIL_WIDTH_STORAGE_KEY);
    else storage?.setItem(RAIL_WIDTH_STORAGE_KEY, String(clampRailWidth(width)));
  } catch { /* The rail remains adjustable when local storage is disabled. */ }
}

export function railWidthForKey(current: number, key: string, shift = false): number | null {
  const step = shift ? RAIL_KEYBOARD_LARGE_STEP : RAIL_KEYBOARD_STEP;
  if (key === "ArrowLeft") return clampRailWidth(current - step);
  if (key === "ArrowRight") return clampRailWidth(current + step);
  return null;
}

/** One shared panel stays open across row changes and across the gap to its content. */
export function createRailHoverController<T>(
  show: (target: T, instant: boolean) => void,
  hide: () => void
) {
  let enterTimer: ReturnType<typeof setTimeout> | undefined;
  let exitTimer: ReturnType<typeof setTimeout> | undefined;
  let shown = false;
  const clear = () => {
    clearTimeout(enterTimer);
    clearTimeout(exitTimer);
  };
  return {
    enter(target: T) {
      clear();
      if (shown) show(target, true);
      else enterTimer = setTimeout(() => {
        shown = true;
        show(target, false);
      }, RAIL_HOVER_ENTER_MS);
    },
    leave() {
      clear();
      exitTimer = setTimeout(() => {
        if (shown) hide();
        shown = false;
      }, RAIL_HOVER_EXIT_MS);
    },
    keepOpen() { clearTimeout(exitTimer); },
    dispose() {
      clear();
      shown = false;
    }
  };
}
