/** The page fold: above the composer zone every line of text is either whole or not shown.
 *
 * A page is read as a run of fine units in document order: a list row, a table row, a
 * checklist item, a paragraph, a heading, a chart, a control row. A unit is shown while it
 * ends above the fold line (the reading area less the composer zone, its solid band and the
 * fade a card's box takes over the band); a unit that would cross the line waits below it,
 * drawn invisible but in place. Cards and sections are never units: a card that continues
 * under the zone keeps its box, and only its own lines wait. A heading waits with the unit it
 * introduces, so it never stands alone. A unit taller than the whole reading area is always
 * shown, since no scroll position could show it whole.
 *
 * The split into units and the decision are pure; the caller measures and applies them. */

/** Elements read as one unit even though they contain others. */
export const PAGE_FOLD_ATOMIC = [
  "h1", "h2", "h3", "p", "li", "tr", "summary", "figure", "pre", "blockquote",
  ".page-band", "[data-recap-slot]", ".ticket-recap", ".card-head", ".proposal-line", ".actions",
  ".metric-top", ".headline", ".chart-range-footer", ".checklist-header", ".group-toggle",
  ".ticket-row", ".manager-workspace-empty", ".step-inner", ".caption", ".goal-meta",
].join(", ");

/** Units that introduce the unit after them. */
export const PAGE_FOLD_HEADS = "h1, h2, h3, thead tr, .caption, .card-head, .metric-top, .proposal-line, .checklist-header, .page-tickets details[open] > summary";

export type FoldUnit = { top: number; bottom: number; head: boolean };

/** Elements that are never units: table column definitions draw no line of their own. */
const PAGE_FOLD_SKIP = "col, colgroup";

/** Splits a page into its units, in document order. A wrapper that draws no box of its own
 *  (display: contents) is read through; an element that draws nothing, or only a screen
 *  reader's 1px box, is skipped. */
export function pageUnits(root: Element): Element[] {
  const units: Element[] = [];
  const visit = (element: Element) => {
    for (const child of element.children) {
      if (!(child instanceof HTMLElement) && !(child instanceof SVGElement)) continue;
      if (child.matches(PAGE_FOLD_SKIP)) continue;
      if (getComputedStyle(child).display === "contents") { visit(child); continue; }
      const box = child.getBoundingClientRect();
      if (box.height <= 1 || box.width <= 1) continue;
      if (child.matches(PAGE_FOLD_ATOMIC) || child.children.length === 0) units.push(child);
      else visit(child);
    }
  };
  visit(root);
  return units;
}

/** Which units wait below the fold. `fold` and `top` are the reading area's fold line and top
 *  edge in the same coordinates as the units. Each unit is decided on its own (two cards side
 *  by side interleave in document order), then headings follow what they introduce. */
export function foldedUnits(units: readonly FoldUnit[], fold: number, top: number): boolean[] {
  const room = fold - top;
  const folded = units.map((unit) => unit.bottom > fold + 0.5 && unit.bottom - unit.top <= room);
  for (let index = units.length - 2; index >= 0; index -= 1) {
    if (units[index].head && folded[index + 1]) folded[index] = true;
  }
  return folded;
}
