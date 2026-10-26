// Adjustable values from the approved left-panel design.
export const RAIL_MIN_WIDTH = 232;
export const RAIL_MAX_WIDTH = 520;
export const RAIL_DEFAULT_WIDTH = "clamp(17rem, 22vw, 20rem)";
// The expanded Settled section includes its header in this share of usable rail height.
export const RAIL_SETTLED_MAX_HEIGHT_SHARE = 0.5;
export const RAIL_WIDTH_STORAGE_KEY = "panels.workspace.rail-width";
export const RAIL_HOVER_ENTER_MS = 200;
export const RAIL_HOVER_EXIT_MS = 90;
export const RAIL_HOVER_RECAP_MAX_LINES = 5;
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
    enter(target: T, immediate = false) {
      clear();
      if (shown || immediate) { shown = true; show(target, true); }
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

export const RAIL_TOUCH_HOLD_MS = 400;
export const RAIL_TOUCH_MOVE_TOLERANCE = 8;

/** Touch uses a hold. Pointer hover is gated before compatibility mouse events exist. */
export function createRailPreviewInput(show: (target: HTMLElement, immediate: boolean) => void, leave: () => void) {
  let hold: ReturnType<typeof setTimeout> | undefined;
  let start: { x: number; y: number; id: number } | null = null;
  let suppressClick = false;
  let pointerPressed = false;
  const cancel = () => { clearTimeout(hold); start = null; pointerPressed = false; };
  return {
    pointerEnter(event: PointerEvent) {
      if (event.pointerType !== "touch" && window.matchMedia("(hover: hover) and (pointer: fine)").matches)
        show(event.currentTarget as HTMLElement, false);
    },
    focus(event: FocusEvent) {
      const focused = event.target as HTMLElement | null;
      const target = focused?.matches ? focused : event.currentTarget as HTMLElement;
      if (!pointerPressed && target.matches(":focus-visible")) show(target, true);
    },
    down(event: PointerEvent) {
      cancel();
      pointerPressed = true;
      suppressClick = false;
      if (event.pointerType !== "touch") return;
      start = {x:event.clientX, y:event.clientY, id:event.pointerId};
      const target = event.currentTarget as HTMLElement;
      hold = setTimeout(() => { if (!target.isConnected) return; suppressClick = true; show(target, true); }, RAIL_TOUCH_HOLD_MS);
    },
    move(event: PointerEvent) {
      if (start && (event.pointerId !== start.id || Math.hypot(event.clientX-start.x,event.clientY-start.y) > RAIL_TOUCH_MOVE_TOLERANCE)) {
        cancel(); suppressClick = false; leave();
      }
    },
    end() { cancel(); },
    leave() { cancel(); leave(); },
    cancel() { cancel(); suppressClick = false; leave(); },
    click(event?: MouseEvent): boolean {
      cancel();
      if (suppressClick) { suppressClick = false; event?.preventDefault(); return false; }
      leave(); return true;
    },
    dispose() { cancel(); suppressClick = false; }
  };
}


/** Native buttons use the same preview input as the shared Worker rows. */
type RailPreviewOptions = { show: (target: HTMLElement, immediate: boolean) => void; leave: () => void };
export function railPreview(node: HTMLElement, options: RailPreviewOptions | null) {
  let current = options;
  const input = createRailPreviewInput((target, immediate) => current?.show(target, immediate), () => current?.leave());
  const listeners: Record<string, EventListener> = {
    pointerenter: (event) => input.pointerEnter(event as PointerEvent),
    pointerleave: () => input.leave(),
    pointerdown: (event) => input.down(event as PointerEvent),
    pointermove: (event) => input.move(event as PointerEvent),
    pointerup: () => input.end(),
    pointercancel: () => input.cancel(),
    focus: (event) => input.focus(event as FocusEvent),
    blur: () => input.leave(),
    click: (event) => { if (!input.click(event as MouseEvent)) event.stopImmediatePropagation(); }
  };
  const capture = (name: string) => ["click", "focus", "blur"].includes(name);
  const guarded = Object.fromEntries(Object.entries(listeners).map(([name, listener]) =>
    [name, ((event: Event) => { if (current) listener(event); }) as EventListener]));
  for (const [name, listener] of Object.entries(guarded)) node.addEventListener(name, listener, capture(name));
  return {
    update(next: typeof options) { current = next; if (!next) input.dispose(); },
    destroy() { input.dispose(); for (const [name, listener] of Object.entries(guarded)) node.removeEventListener(name, listener, capture(name)); }
  };
}
