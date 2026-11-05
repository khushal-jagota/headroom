import type { LayoutRectangle } from './conversation/layout/contracts';

/** Input types that never raise the on-screen keyboard. */
const NON_TEXT_INPUT_TYPES = new Set(['button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit']);

/** Frames the visual viewport must hold still before the keyboard counts as settled. */
const SETTLED_FRAMES = 10;
/** The longest a keyboard or rotation movement is followed, in frames. */
const MAX_FOLLOW_FRAMES = 90;

export interface ViewportReading {
  textEntryFocused: boolean;
  layoutWidth: number;
  layoutHeight: number;
  visual: LayoutRectangle | null;
}

/** True when the focused element raises the on-screen keyboard or a picker. */
function textEntryFocused(): boolean {
  const element = document.activeElement;
  if (element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) return true;
  if (element instanceof HTMLInputElement) return !NON_TEXT_INPUT_TYPES.has(element.type);
  return element instanceof HTMLElement && element.isContentEditable;
}

function readViewport(): ViewportReading {
  const visual = window.visualViewport;
  return {
    textEntryFocused: textEntryFocused(),
    layoutWidth: document.documentElement.clientWidth,
    layoutHeight: document.documentElement.clientHeight,
    visual: visual ? { top: visual.offsetTop, left: visual.offsetLeft, width: visual.width, height: visual.height } : null,
  };
}

/**
 * The part of the layout viewport the reader can see. Only an on-screen keyboard narrows
 * it. An open conversation menu retains those bounds when it takes focus from the
 * text field. Without either, the whole layout viewport is visible, whatever size
 * iPhone Safari last reported: it can report
 * the keyboard-sized screen one last time as the keyboard closes and never again.
 */
export function visibleViewportFrom(reading: ViewportReading, menuOpen = false): LayoutRectangle {
  if ((!reading.textEntryFocused && !menuOpen) || !reading.visual) return { top: 0, left: 0, width: reading.layoutWidth, height: reading.layoutHeight };
  return reading.visual;
}

export function visibleViewport(menuOpen = false): LayoutRectangle {
  return visibleViewportFrom(readViewport(), menuOpen);
}

/** How far the keyboard reaches up from the bottom of the layout viewport. */
export function keyboardInsetBottom(): number {
  const reading = readViewport();
  const visible = visibleViewportFrom(reading);
  return Math.max(0, reading.layoutHeight - visible.top - visible.height);
}

/**
 * Call `listener` whenever the visible viewport can have changed: the keyboard opens or
 * closes, the phone rotates, or the app returns to the screen. The keyboard keeps moving
 * after the event that starts it, so the listener runs each frame until it holds still.
 * When the keyboard has closed, a page that cannot scroll is put back at its top, because
 * iPhone Safari can leave it pushed up behind the closed keyboard.
 */
export function observeVisibleViewport(listener: () => void): () => void {
  let frame = 0;
  let followed = 0;
  let still = 0;
  let last = '';
  function step(): void {
    frame = 0;
    const visual = window.visualViewport;
    const key = `${visual?.offsetTop},${visual?.offsetLeft},${visual?.width},${visual?.height},${document.documentElement.clientWidth},${document.documentElement.clientHeight},${textEntryFocused()}`;
    still = key === last ? still + 1 : 0;
    last = key;
    const root = document.scrollingElement ?? document.documentElement;
    if (!textEntryFocused() && root.scrollHeight <= root.clientHeight && (window.scrollX || window.scrollY)) window.scrollTo(0, 0);
    listener();
    followed += 1;
    if (still < SETTLED_FRAMES && followed < MAX_FOLLOW_FRAMES) frame = requestAnimationFrame(step);
  }
  function follow(): void {
    followed = 0;
    still = 0;
    if (!frame) frame = requestAnimationFrame(step);
  }
  function visibility(): void { if (document.visibilityState === 'visible') follow(); }
  const visual = window.visualViewport;
  visual?.addEventListener('resize', follow);
  visual?.addEventListener('scroll', follow);
  window.addEventListener('resize', follow);
  window.addEventListener('pageshow', follow);
  document.addEventListener('focusin', follow);
  document.addEventListener('focusout', follow);
  document.addEventListener('visibilitychange', visibility);
  return () => {
    cancelAnimationFrame(frame);
    visual?.removeEventListener('resize', follow);
    visual?.removeEventListener('scroll', follow);
    window.removeEventListener('resize', follow);
    window.removeEventListener('pageshow', follow);
    document.removeEventListener('focusin', follow);
    document.removeEventListener('focusout', follow);
    document.removeEventListener('visibilitychange', visibility);
  };
}
