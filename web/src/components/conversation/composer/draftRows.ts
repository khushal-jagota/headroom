/** The composer's one row, and the row its controls move to under a long draft.
 *
 * At rest, and while a draft is short, the composer is one row: the add button, the draft,
 * then the model, delivery, voice and send controls. A draft that comes within
 * DRAFT_ROWS_STACK_WITHIN of those controls, or holds a line break, would otherwise wrap in
 * the narrow space beside them, so the controls go to a row of their own under it and the
 * draft takes the full width on top. They come back only once the draft fits beside them
 * with DRAFT_ROWS_UNSTACK_SPARE to spare, or is cleared, so the composer never flickers
 * between the two while somebody types at the edge.
 *
 * Where the one row would leave the draft less than DRAFT_ROWS_MIN_ROOM beside the controls
 * (a phone, a tablet's column, a narrow window), there is no one row: the draft has its own
 * row at rest too, so the box is never a sliver.
 *
 * What decides is the draft and the room it has beside the controls. Nothing here reads the
 * viewport.
 */

export const DRAFT_ROWS_MIN_ROOM = 240;
export const DRAFT_ROWS_STACK_WITHIN = 40;
export const DRAFT_ROWS_UNSTACK_SPARE = 80;
export const DRAFT_ROWS_MOTION_MS = 180;

/** Whether the controls belong under the draft, given where they are now. */
export function controlsBelowTheDraft(input: {
  controlsBelow: boolean;
  text: string;
  /** The draft's width written on one line. */
  draftWidth: number;
  /** The width the draft has beside the controls on the one row. */
  roomBesideControls: number;
}): boolean {
  if (input.roomBesideControls < DRAFT_ROWS_MIN_ROOM) return true;
  if (input.text === "") return false;
  if (input.text.includes("\n")) return true;
  const margin = input.controlsBelow ? DRAFT_ROWS_UNSTACK_SPARE : DRAFT_ROWS_STACK_WITHIN;
  return input.draftWidth + margin > input.roomBesideControls;
}

// The properties that decide how wide a line of the draft is.
const lineProperties = [
  "font-family", "font-size", "font-weight", "font-style", "font-stretch", "font-variant",
  "font-feature-settings", "font-variation-settings", "font-kerning", "font-optical-sizing",
  "font-size-adjust", "font-synthesis", "letter-spacing", "word-spacing", "text-transform",
  "text-rendering", "tab-size"
] as const;

/** Measure how wide a draft is on one line, in the textarea's own type, off screen. */
export function createDraftLineMeasurement(document: Document) {
  const line = document.createElement("span");
  line.setAttribute("aria-hidden", "true");
  line.setAttribute("data-composer-line-measurement", "");
  line.style.cssText = "position:fixed;top:0;left:0;visibility:hidden;pointer-events:none;"
    + "white-space:pre;margin:0;padding:0;border:0;";
  document.body.append(line);
  return {
    measure(text: string, style: CSSStyleDeclaration): number {
      for (const property of lineProperties) {
        line.style.setProperty(property, style.getPropertyValue(property));
      }
      line.textContent = text;
      return line.getBoundingClientRect().width;
    },
    dispose(): void {
      line.remove();
    }
  };
}

/** Move between the two layouts, animating the box's height and everything that moved.
 *
 * Every position is read before `change` and again after it, and each moved element is
 * drawn from where it was to where it now is (the box's height likewise), so nothing is
 * ever painted in a place it is not passing through. Positions are read with any earlier
 * move still running, so a reversal starts from where things are on screen.
 */
export function moveBetweenDraftRows(
  box: HTMLElement,
  movers: readonly HTMLElement[],
  running: readonly Animation[],
  change: () => void,
  timing: { duration: number; easing: string }
): Animation[] {
  const before = movers.map((element) => element.getBoundingClientRect());
  const boxBefore = box.getBoundingClientRect().height;
  for (const animation of running) animation.cancel();
  change();
  const boxAfter = box.getBoundingClientRect().height;
  const started: Animation[] = [];
  if (Math.abs(boxAfter - boxBefore) >= 0.5) {
    started.push(box.animate(
      [{ height: `${boxBefore}px` }, { height: `${boxAfter}px` }],
      timing
    ));
  }
  movers.forEach((element, index) => {
    const after = element.getBoundingClientRect();
    const dx = before[index].left - after.left;
    const dy = before[index].top - after.top;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
    started.push(element.animate(
      [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
      timing
    ));
  });
  return started;
}
