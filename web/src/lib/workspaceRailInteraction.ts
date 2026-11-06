// Adjustable values from the approved left-panel design.
export const RAIL_MIN_WIDTH = 232;
export const RAIL_MAX_WIDTH = 520;
export const RAIL_DEFAULT_WIDTH = "clamp(17rem, 22vw, 20rem)";
// The expanded Settled section includes its header in this share of usable rail height.
export const RAIL_SETTLED_MAX_HEIGHT_SHARE = 0.5;
export const RAIL_WIDTH_STORAGE_KEY = "panels.workspace.rail-width";
export const RAIL_HOVER_ENTER_MS = 200;
export const RAIL_HOVER_EXIT_MS = 90;
export const RAIL_HOVER_RECAP_MAX_LINES = 2;
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

/** The longest run of whole words, then an ellipsis, that fits in ``lines`` lines of ``node``.
 * A recap that runs out ends at a word, never part of one. */
type WordClampOptions = { text: string; lines: number };
export function clampToWords(node: HTMLElement, options: WordClampOptions) {
  let current = options;
  let frame = 0;
  const fits = () => node.scrollHeight <= Math.ceil(current.lines * (Number.parseFloat(getComputedStyle(node).lineHeight) || 20)) + 1;
  const apply = () => {
    frame = 0;
    const text = current.text.trim();
    node.textContent = text;
    if (current.lines <= 0 || !node.isConnected || fits()) return;
    const words = text.split(/\s+/);
    let low = 0, high = words.length - 1;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      node.textContent = `${words.slice(0, middle).join(" ").replace(/[\s,;:.\-–—]+$/, "")}…`;
      if (fits()) low = middle; else high = middle - 1;
    }
    node.textContent = `${words.slice(0, Math.max(1, low)).join(" ").replace(/[\s,;:.\-–—]+$/, "")}…`;
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(apply); };
  // Only a change of width can change where the lines break.
  let width = node.clientWidth;
  const observer = new ResizeObserver(() => { if (node.clientWidth !== width) { width = node.clientWidth; schedule(); } });
  observer.observe(node);
  apply();
  void document.fonts?.ready.then(schedule);
  return {
    update(next: WordClampOptions) { current = next; apply(); },
    destroy() { observer.disconnect(); if (frame) cancelAnimationFrame(frame); }
  };
}

/** A rail title too long for its row fades out at the row's text edge instead of ending in a
 * cut word and an ellipsis. This marks the titles that run out (data-title-overflow); the
 * stylesheet draws the fade. The text itself is never changed. */
const RAIL_TITLES = ".item-title, .ticket-row-title, .row-title, .manager-eyebrow > span, .settled-title > span";
export function railTitleFade(root: HTMLElement) {
  let frame = 0;
  const run = () => {
    frame = 0;
    for (const title of root.querySelectorAll<HTMLElement>(RAIL_TITLES))
      title.toggleAttribute("data-title-overflow", title.scrollWidth > title.clientWidth + 0.5);
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(run); };
  // A title runs out when its row narrows or its words change.
  const resize = new ResizeObserver(schedule);
  const watch = () => { resize.disconnect(); resize.observe(root); for (const title of root.querySelectorAll<HTMLElement>(RAIL_TITLES)) resize.observe(title); schedule(); };
  const mutation = new MutationObserver(watch);
  mutation.observe(root, { subtree: true, childList: true, characterData: true });
  watch();
  void document.fonts?.ready.then(schedule);
  return { destroy() { resize.disconnect(); mutation.disconnect(); if (frame) cancelAnimationFrame(frame); } };
}

/** The rail's list edges. At the top, a row (a header, a goal, a Ticket) that would cross the
 * edge waits until it fits, and a group heading waits with its first row. At the bottom, rows
 * run on under the edge and its short fade, as the references' lists do, and a heading waits
 * only while less than half of its first row would show, so the list never ends on a heading
 * without its rows. Waiting units keep their place; the stylesheet hides them
 * (data-rail-waiting). */
const RAIL_UNITS = ".identity, .goal, .worker-row, .bucket-label";
export function railWholeRows(list: HTMLElement) {
  let frame = 0;
  const run = () => {
    frame = 0;
    const box = list.getBoundingClientRect();
    const top = box.top + list.clientTop - 0.5, bottom = box.top + list.clientTop + list.clientHeight + 0.5;
    const clearOfTop = (rect: DOMRect) => rect.height > 0 && rect.top >= top;
    for (const unit of list.querySelectorAll<HTMLElement>(RAIL_UNITS)) {
      let shown = clearOfTop(unit.getBoundingClientRect());
      if (shown && unit.matches(".bucket-label")) {
        const first = unit.nextElementSibling?.getBoundingClientRect();
        shown = !first || (clearOfTop(first) && Math.min(first.bottom, bottom) - first.top >= first.height / 2);
      }
      unit.toggleAttribute("data-rail-waiting", !shown);
    }
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(run); };
  const resize = new ResizeObserver(schedule);
  resize.observe(list);
  const mutation = new MutationObserver(schedule);
  mutation.observe(list, { subtree: true, childList: true });
  list.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  schedule();
  return { destroy() { resize.disconnect(); mutation.disconnect(); list.removeEventListener("scroll", schedule); window.removeEventListener("resize", schedule); if (frame) cancelAnimationFrame(frame); } };
}

/** When a page opens, the rail shows where it is: the list scrolls just far enough that the
 * whole block of the Manager you are inside is in view, frame included, if it fits; otherwise
 * that the open item is. Standard reveal behaviour; it moves nothing that is already in view.
 * ``key`` names the open page, so the reveal runs once each time a page opens. */
export function railReveal(list: HTMLElement, key: string) {
  let current = key;
  let frame = 0;
  // The rail's rows arrive after the page does, so for a short while after a page opens the
  // reveal follows the list as it settles; anything the person does to the list ends it.
  let settleUntil = 0;
  const css = (name: string) => Number.parseFloat(getComputedStyle(list).getPropertyValue(name)) || 0;
  const reveal = () => {
    frame = 0;
    if (performance.now() > settleUntil) return;
    const open = list.querySelector<HTMLElement>('.manager-card [aria-current="page"]');
    if (!open) return;
    const view = list.getBoundingClientRect();
    const viewTop = view.top + list.clientTop, viewBottom = viewTop + list.clientHeight;
    const outset = css("--rail-context-outset");
    const below = outset + css("--rail-edge-fade");
    const block = open.closest<HTMLElement>(".manager-card")?.getBoundingClientRect();
    const fill = (open.closest<HTMLElement>(".identity") ?? open).getBoundingClientRect();
    const target = block && block.height + outset + below <= list.clientHeight ? block : fill;
    const top = target.top - outset, bottom = target.bottom + below;
    const delta = top < viewTop ? top - viewTop : bottom > viewBottom ? bottom - viewBottom : 0;
    if (delta) list.scrollTop += delta;
  };
  const schedule = () => { if (!frame && performance.now() <= settleUntil) frame = requestAnimationFrame(reveal); };
  const start = () => { settleUntil = performance.now() + 2000; schedule(); };
  const stop = () => { settleUntil = 0; };
  const resize = new ResizeObserver(schedule);
  const watch = () => { resize.disconnect(); resize.observe(list); for (const card of list.querySelectorAll(".manager-card")) resize.observe(card); schedule(); };
  const mutation = new MutationObserver(watch);
  mutation.observe(list, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-current"] });
  for (const name of ["wheel", "touchstart", "keydown", "pointerdown"]) list.addEventListener(name, stop, { passive: true });
  watch();
  start();
  return {
    update(next: string) { if (next !== current) { current = next; start(); } },
    destroy() { cancelAnimationFrame(frame); resize.disconnect(); mutation.disconnect(); for (const name of ["wheel", "touchstart", "keydown", "pointerdown"]) list.removeEventListener(name, stop); }
  };
}
