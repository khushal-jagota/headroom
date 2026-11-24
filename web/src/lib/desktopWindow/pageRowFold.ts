/* When a page row is too narrow, its context line shortens first, down to a floor; then its
 * actions fold, last first, into a "•••" button at the row's end. Framework-free, so the rule
 * is tested without a page. */

export type PageRowMeasure = {
  /** The width the row has. */
  rowWidth: number;
  /** The context line's full width (0 when the row has no context line). */
  contextWidth: number;
  /** The floor the context line may shorten to. */
  contextMinWidth: number;
  /** Each action's width, in the order the row shows them. */
  actionWidths: readonly number[];
  /** The space between two actions. */
  actionGap: number;
  /** The space between the context line and the actions. */
  rowGap: number;
  /** The "•••" button's width. */
  moreWidth: number;
};

/** How many actions, counted from the last, fold into the "•••" menu. */
export function foldedActionCount(measure: PageRowMeasure): number {
  const context = Math.min(measure.contextWidth, measure.contextMinWidth);
  const fits = (shown: number): boolean => {
    const folded = shown < measure.actionWidths.length;
    const parts = [...measure.actionWidths.slice(0, shown), ...(folded ? [measure.moreWidth] : [])];
    const actions = parts.reduce((sum, width) => sum + width, 0) + Math.max(0, parts.length - 1) * measure.actionGap;
    const gap = context > 0 && parts.length > 0 ? measure.rowGap : 0;
    return context + gap + actions <= measure.rowWidth;
  };
  for (let shown = measure.actionWidths.length; shown > 0; shown--) {
    if (fits(shown)) return measure.actionWidths.length - shown;
  }
  return measure.actionWidths.length;
}
