// Adjustable values from the approved left-panel design.
export const RAIL_MIN_WIDTH = 232;
export const RAIL_MAX_WIDTH = 520;
export const RAIL_DEFAULT_WIDTH = "clamp(17rem, 22vw, 20rem)";
// The expanded Settled section includes its header in this share of usable rail height.
export const RAIL_SETTLED_MAX_HEIGHT_SHARE = 0.5;
export const RAIL_WIDTH_STORAGE_KEY = "panels.workspace.rail-width";
export const RAIL_HOVER_ENTER_MS = 200;
export const RAIL_HOVER_EXIT_MS = 90;
export const RAIL_HOVER_RECAP_MAX_LINES = 4;
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

const RAIL_SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End", " "]);

/**
 * A row, manager or Chief preview leaves when the person scrolls: the wheel, a touch
 * drag past the hold tolerance, or a scroll key outside a text field. A scroll event
 * alone does not dismiss it. Layout, data refreshes and the browser itself also move
 * scroll positions, and a pointer that stays still on a row gets no second
 * pointerenter to bring the preview back.
 */
export function listenForUserScroll(page: EventTarget, scrolled: (event: Event) => void): () => void {
  const wheel = (event: Event) => scrolled(event);
  // A finger that holds a row to open its preview trembles a little. Only a drag counts.
  let touchStart: { x: number; y: number } | null = null;
  const touchPoint = (event: Event) => (event as TouchEvent).touches?.[0];
  const touchDown = (event: Event) => { const point = touchPoint(event); touchStart = point ? { x: point.clientX, y: point.clientY } : null; };
  const touchMove = (event: Event) => {
    const point = touchPoint(event);
    if (!touchStart || !point) return;
    if (Math.hypot(point.clientX - touchStart.x, point.clientY - touchStart.y) <= RAIL_TOUCH_MOVE_TOLERANCE) return;
    touchStart = null;
    scrolled(event);
  };
  const key = (event: Event) => {
    const press = event as KeyboardEvent;
    const source = press.target as HTMLElement | null;
    const closest = (selector: string) => typeof source?.closest === "function" && source.closest(selector) !== null;
    if (press.defaultPrevented || press.altKey || press.ctrlKey || press.metaKey || !RAIL_SCROLL_KEYS.has(press.key)) return;
    if (closest('input,textarea,select,[contenteditable="true"]') || (press.key === " " && closest("button,summary,a"))) return;
    scrolled(press);
  };
  page.addEventListener("wheel", wheel, { passive: true });
  page.addEventListener("touchstart", touchDown, { passive: true });
  page.addEventListener("touchmove", touchMove, { passive: true });
  page.addEventListener("keydown", key);
  return () => {
    page.removeEventListener("wheel", wheel);
    page.removeEventListener("touchstart", touchDown);
    page.removeEventListener("touchmove", touchMove);
    page.removeEventListener("keydown", key);
  };
}

/**
 * Touch uses a hold. Pointer hover is gated before compatibility mouse events exist.
 * Lifting the finger after a completed hold fires a touch pointerleave, and the release
 * can blur the row to nowhere. Neither is the person leaving: the held preview stays
 * until the screen dismisses it on an outside press or a scroll, or another row opens.
 */
export function createRailPreviewInput(show: (target: HTMLElement, immediate: boolean) => void, leave: () => void) {
  let hold: ReturnType<typeof setTimeout> | undefined;
  let start: { x: number; y: number; id: number } | null = null;
  let suppressClick = false;
  let pointerPressed = false;
  let held = false;
  const cancel = () => { clearTimeout(hold); start = null; pointerPressed = false; };
  const releaseOfHold = (event?: Event) => held && (
    (event?.type === "pointerleave" && (event as PointerEvent).pointerType === "touch")
    || (event?.type === "blur" && ((event as FocusEvent).relatedTarget === null || (event as FocusEvent).relatedTarget === document.body)));
  return {
    pointerEnter(event: PointerEvent) {
      if (event.pointerType !== "touch" && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
        held = false; show(event.currentTarget as HTMLElement, false);
      }
    },
    focus(event: FocusEvent) {
      const focused = event.target as HTMLElement | null;
      const target = focused?.matches ? focused : event.currentTarget as HTMLElement;
      if (!pointerPressed && target.matches(":focus-visible")) { held = false; show(target, true); }
    },
    down(event: PointerEvent) {
      cancel();
      pointerPressed = true;
      suppressClick = false;
      held = false;
      if (event.pointerType !== "touch") return;
      start = {x:event.clientX, y:event.clientY, id:event.pointerId};
      const target = event.currentTarget as HTMLElement;
      hold = setTimeout(() => { if (!target.isConnected) return; suppressClick = true; held = true; show(target, true); }, RAIL_TOUCH_HOLD_MS);
    },
    move(event: PointerEvent) {
      if (start && (event.pointerId !== start.id || Math.hypot(event.clientX-start.x,event.clientY-start.y) > RAIL_TOUCH_MOVE_TOLERANCE)) {
        cancel(); suppressClick = false; held = false; leave();
      }
    },
    end() { cancel(); },
    /** Pass the pointerleave or blur, so the release of a completed hold is kept. */
    leave(event?: Event) { cancel(); if (!releaseOfHold(event)) leave(); },
    cancel() { cancel(); suppressClick = false; held = false; leave(); },
    click(event?: MouseEvent): boolean {
      cancel();
      if (suppressClick) { suppressClick = false; event?.preventDefault(); return false; }
      held = false; leave(); return true;
    },
    dispose() { cancel(); suppressClick = false; held = false; }
  };
}


/** Native buttons use the same preview input as the shared Worker rows. */
type RailPreviewOptions = { show: (target: HTMLElement, immediate: boolean) => void; leave: () => void };
export function railPreview(node: HTMLElement, options: RailPreviewOptions | null) {
  let current = options;
  const input = createRailPreviewInput((target, immediate) => current?.show(target, immediate), () => current?.leave());
  const listeners: Record<string, EventListener> = {
    pointerenter: (event) => input.pointerEnter(event as PointerEvent),
    pointerleave: (event) => input.leave(event),
    pointerdown: (event) => input.down(event as PointerEvent),
    pointermove: (event) => input.move(event as PointerEvent),
    pointerup: () => input.end(),
    pointercancel: () => input.cancel(),
    focus: (event) => input.focus(event as FocusEvent),
    blur: (event) => input.leave(event),
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
