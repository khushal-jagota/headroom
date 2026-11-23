import type { TransitionConfig } from "svelte/transition";

/** The review page's Directional Deck.
 *
 * Items move on the horizontal axis, always the same way: the old view leaves left and the
 * new one arrives from the right, including the way back from the final screen. The artifact is a
 * layer on the vertical axis: it rises to open and drops to close.
 *
 * Every distance, duration and curve comes from `assets/tokens.css`. The one value that is
 * not a token is how long an arrival waits after the exit has begun.
 */

/** The key a page gives its deck under, so the cards inside it can take part in its moves. */
export const DECK_CONTEXT = Symbol("review-deck");

/** The next item starts this long after the last one starts to leave. */
export const ARRIVAL_DELAY_MS = 60;

/** The view key of the final screen. Every other key is an item's own. */
export const FINAL_VIEW = "final";

type Easing = (progress: number) => number;

/** The curve a CSS `cubic-bezier()` draws, for the places a framework asks for a function. */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): Easing {
  const x = (s: number) => 3 * (1 - s) * (1 - s) * s * x1 + 3 * (1 - s) * s * s * x2 + s * s * s;
  const y = (s: number) => 3 * (1 - s) * (1 - s) * s * y1 + 3 * (1 - s) * s * s * y2 + s * s * s;
  return (progress) => {
    if (progress <= 0) return 0;
    if (progress >= 1) return 1;
    let low = 0;
    let high = 1;
    let s = progress;
    for (let step = 0; step < 24; step += 1) {
      s = (low + high) / 2;
      if (x(s) < progress) low = s;
      else high = s;
    }
    return y(s);
  };
}

export interface DeckTokens {
  /** How far an item travels. */
  distance: number;
  /** How far the artifact layer travels. */
  layerRise: number;
  exit: number;
  enter: number;
  fade: number;
  /** The gap-closing duration. */
  collapse: number;
  exitEase: Easing;
  enterEase: Easing;
  /** The curve that closes a gap, as a function and as the CSS value for animations written straight to the element. */
  move: Easing;
  moveEase: string;
}

function lengthToken(style: CSSStyleDeclaration, name: string): number {
  return parseFloat(style.getPropertyValue(name)) || 0;
}

function durationToken(style: CSSStyleDeclaration, name: string): number {
  const value = style.getPropertyValue(name).trim();
  return (parseFloat(value) || 0) * (value.endsWith("ms") ? 1 : 1000);
}

function easingToken(style: CSSStyleDeclaration, name: string): Easing {
  const numbers = style.getPropertyValue(name).match(/-?\d*\.?\d+/g)?.map(Number) ?? [];
  return numbers.length === 4 ? cubicBezier(numbers[0], numbers[1], numbers[2], numbers[3]) : (progress) => progress;
}

/** The tokens in force where the element is drawn, including any override above it. */
export function readDeckTokens(element: Element): DeckTokens {
  const style = getComputedStyle(element);
  return {
    distance: lengthToken(style, "--space-6"),
    layerRise: lengthToken(style, "--space-5"),
    exit: durationToken(style, "--motion-base"),
    enter: durationToken(style, "--motion-slow"),
    fade: durationToken(style, "--motion-fast"),
    collapse: durationToken(style, "--motion-slow"),
    exitEase: easingToken(style, "--motion-ease-exit"),
    enterEase: easingToken(style, "--motion-ease"),
    move: easingToken(style, "--motion-move"),
    moveEase: style.getPropertyValue("--motion-move").trim() || "ease-in-out"
  };
}

export function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const LEAVING = "data-deck-leaving";

/** Stop everything the deck has running. Nothing waits on an old animation: one that is
 *  leaving jumps to its end, and one that is arriving jumps to its resting place. */
export function settleDeck(root: Element | null): void {
  if (!root) return;
  for (const element of root.querySelectorAll("[data-deck-item], [data-deck-part], [data-deck-layer], [data-deck-shift]")) {
    const leaving = element.hasAttribute(LEAVING);
    for (const animation of element.getAnimations()) {
      try {
        if (leaving) animation.finish();
        else animation.cancel();
      } catch {
        // An animation that cannot finish has nothing left to settle.
      }
    }
  }
}

/** Hold a leaving element exactly where it stands on the screen, at the size it has.
 *
 *  The element stays the card it is: same node, same parents, so every rule that styles it
 *  still applies. It only stops taking part in the layout, so the next item can take the
 *  top of the scroll area while this one slides out. */
function pin(node: HTMLElement, stood: Stood, resetScroll: boolean): void {
  const { onScreen, contentTop } = stood;
  node.setAttribute(LEAVING, "");
  node.setAttribute("aria-hidden", "true");
  node.style.position = "absolute";
  node.style.margin = "0";
  node.style.pointerEvents = "none";
  if (resetScroll) {
    const scroller = node.closest<HTMLElement>("[data-deck-scroll]");
    if (scroller) scroller.scrollTop = 0;
  }
  const holder = (node.offsetParent as HTMLElement | null) ?? document.documentElement;
  const holderBox = holder.getBoundingClientRect();
  node.style.left = `${onScreen.left - holderBox.left - holder.clientLeft + holder.scrollLeft}px`;
  const top = onScreen.top - holderBox.top - holder.clientTop + holder.scrollTop;
  node.style.top = `${top}px`;
  node.style.width = `${onScreen.width}px`;
  node.style.height = `${onScreen.height}px`;
  // A margin that ran out through the element's top edge now stays inside it, which would push
  // the content down. The content is what must stay where it stood.
  const now = node.firstElementChild?.getBoundingClientRect().top;
  if (contentTop !== null && now !== undefined && Math.abs(now - contentTop) > 0.01) {
    node.style.top = `${top + contentTop - now}px`;
  }
}

/** Where an element stood on the screen, and where its first content stood inside it. */
interface Stood {
  onScreen: DOMRect;
  contentTop: number | null;
}

function standing(node: HTMLElement): Stood {
  return { onScreen: node.getBoundingClientRect(), contentTop: node.firstElementChild?.getBoundingClientRect().top ?? null };
}

/** What the deck remembers between renders. The page changes it, the transitions read it. */
export class Deck {
  /** False for the first thing drawn, which has nothing to arrive after. */
  hadPrevious = false;
  /** True from when the artifact layer starts to rise until it is fully gone. */
  covering = false;
  /** How the artifact layer leaves: it drops, or it goes left like a card. */
  layerExit: "drop" | "left" = "drop";

  #view: string | null = null;
  #leaving: { node: HTMLElement; stood: Stood } | null = null;

  /** Call before the page draws the next state, while the screen still shows the last one.
   *  The leaving item is measured here: once the next item is in the page, it is too late. */
  beforeChange(root: Element | null, view: string, itemNode: HTMLElement | null): void {
    settleDeck(root);
    if (view === this.#view) return;
    const previous = this.#view;
    this.#view = view;
    this.hadPrevious = previous !== null;
    this.#leaving = previous !== null && itemNode ? { node: itemNode, stood: standing(itemNode) } : null;
  }

  /** Where the leaving item stood before the page changed. */
  stood(node: HTMLElement): Stood {
    return this.#leaving?.node === node ? this.#leaving.stood : standing(node);
  }
}

type DeckParams = { deck: Deck };

/** An item leaves: it stays where it was and slides out. */
export function deckItemOut(node: HTMLElement, { deck }: DeckParams): TransitionConfig {
  const tokens = readDeckTokens(node);
  pin(node, deck.stood(node), true);
  // Under an artifact the old card is never seen again, only the artifact leaving.
  if (deck.covering) node.style.visibility = "hidden";
  if (reducedMotion()) return { duration: tokens.fade, css: (t) => `opacity:${t}` };
  return {
    duration: tokens.exit,
    easing: tokens.exitEase,
    css: (t, u) => `opacity:${t};transform:translateX(${-tokens.distance * u}px)`
  };
}

/** The next item arrives from the right while the last one is still leaving. */
export function deckItemIn(node: HTMLElement, { deck }: DeckParams): TransitionConfig {
  if (!deck.hadPrevious) return { duration: 0 };
  const tokens = readDeckTokens(node);
  if (reducedMotion()) return { duration: tokens.fade, css: (t) => `opacity:${t}` };
  return {
    delay: ARRIVAL_DELAY_MS,
    duration: tokens.enter,
    easing: tokens.enterEase,
    css: (t, u) => `opacity:${t};transform:translateX(${tokens.distance * u}px)`
  };
}

/** One part of a mixed Ticket leaves left and the rest of the card closes the gap.
 *
 *  The part is pinned where it stood, so what follows it is at once where it will end.
 *  Each follower then starts at its old place and glides up once the part has gone. */
export function deckPartOut(node: HTMLElement, { deck }: DeckParams): TransitionConfig {
  // A part decided under an artifact is already gone from the card when the layer drops.
  if (deck.covering) return { duration: 0 };
  const tokens = readDeckTokens(node);
  if (reducedMotion()) {
    pin(node, standing(node), false);
    return { duration: tokens.fade, css: (t) => `opacity:${t}` };
  }
  const followers: HTMLElement[] = [];
  for (let next = node.nextElementSibling; next; next = next.nextElementSibling) followers.push(next as HTMLElement);
  const stood = standing(node);
  const before = followers[0]?.getBoundingClientRect().top ?? 0;
  pin(node, stood, false);
  const closed = before - (followers[0]?.getBoundingClientRect().top ?? 0);
  if (closed !== 0) {
    for (const follower of followers) {
      follower.setAttribute("data-deck-shift", "");
      const slide = follower.animate(
        [{ transform: `translateY(${closed}px)` }, { transform: "none" }],
        { duration: tokens.collapse, delay: tokens.exit, easing: tokens.moveEase, fill: "both" }
      );
      slide.onfinish = slide.oncancel = () => {
        slide.cancel();
        follower.removeAttribute("data-deck-shift");
      };
    }
  }
  return {
    duration: tokens.exit,
    easing: tokens.exitEase,
    css: (t, u) => `opacity:${t};transform:translateX(${-tokens.distance * u}px)`
  };
}

/** What belongs to a part but stands outside it, such as the request for a recap that only a
 *  card with messages carries. It closes its own space once the part has left, so the card
 *  below it glides up with the rest instead of jumping. */
export function deckGapOut(node: HTMLElement, { deck }: DeckParams): TransitionConfig {
  if (deck.covering || reducedMotion()) return { duration: 0 };
  node.setAttribute(LEAVING, "");
  const tokens = readDeckTokens(node);
  const style = getComputedStyle(node);
  const height = node.offsetHeight;
  const marginTop = parseFloat(style.marginTop) || 0;
  const marginBottom = parseFloat(style.marginBottom) || 0;
  return {
    delay: tokens.exit,
    duration: tokens.collapse,
    easing: tokens.move,
    css: (t) => `overflow:hidden;opacity:${t};height:${height * t}px;margin-top:${marginTop * t}px;margin-bottom:${marginBottom * t}px`
  };
}

/** The artifact layer rises to open. */
export function deckLayerIn(node: HTMLElement, { deck }: DeckParams): TransitionConfig {
  deck.covering = true;
  const tokens = readDeckTokens(node);
  if (reducedMotion()) return { duration: tokens.fade, css: (t) => `opacity:${t}` };
  return {
    duration: tokens.enter,
    easing: tokens.enterEase,
    css: (t, u) => `opacity:${t};transform:translateY(${tokens.layerRise * u}px)`
  };
}

/** The artifact layer drops to close. When the decision made inside it finished the item,
 *  it goes left like a card instead, and the next item arrives from the right. */
export function deckLayerOut(node: HTMLElement, { deck }: DeckParams): TransitionConfig {
  node.setAttribute(LEAVING, "");
  const tokens = readDeckTokens(node);
  const left = deck.layerExit === "left";
  deck.layerExit = "drop";
  if (reducedMotion()) return { duration: tokens.fade, css: (t) => `opacity:${t}` };
  return {
    duration: tokens.exit,
    easing: tokens.exitEase,
    css: left
      ? (t, u) => `opacity:${t};transform:translateX(${-tokens.distance * u}px)`
      : (t, u) => `opacity:${t};transform:translateY(${tokens.layerRise * u}px)`
  };
}
