/* The desktop window's tunable numbers. Lengths that CSS can hold are tokens in
 * assets/tokens.css (--window-top-row and its neighbours); these are the ones the script uses. */

/** A page row's context line shortens, with an ellipsis, down to this width before any action folds. */
export const PAGE_ROW_CONTEXT_MIN_WIDTH_PX = 140;

/** The gap between a page row's context line and its actions (12px, the row's flex gap). */
export const PAGE_ROW_GAP_PX = 12;

/** With the sidebar hidden, the reading column stays at least this far right of the sidebar button. */
export const READING_COLUMN_CLEARANCE_PX = 20;

/** The pause before the sidebar button names itself, as macOS shows a button's name. */
export const TOOLTIP_DELAY_MS = 600;
