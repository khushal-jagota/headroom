/** Where a message's hover row (its time and copy button) stands (owner's review, B9; hands-on
 *  test 1, items 2, 3 and 6). The row is a 24 px slot. It never adds space, never covers
 *  words, and never touches what follows.
 *
 *  - Below: the gap under the message holds the slot 4 px under the message with 4 px to
 *    spare before the next item (a turn's 32). The row stands there.
 *  - Beside: no room below (a tool row, a system line or the next message stands closer). The
 *    row stands level with the message's last line, in the slot the caller names: the column's
 *    right end for the agent's words, just left of the bubble for the person's. When that line
 *    runs into the slot, the row goes up to the lowest line that leaves it clear.
 *  - Nowhere: no line leaves the slot clear. Copy stays in the Tab order. */

export const HOVER_ROW_OFFSET_PIXELS = 4;
export const HOVER_ROW_HEIGHT_PIXELS = 24;
/** Room kept clear between the row and the item that follows it. */
export const HOVER_ROW_CLEARANCE_PIXELS = 4;

export type Box = { left: number; right: number; top: number; bottom: number };

export type HoverRowGeometry = {
  messageBottom: number;
  /** Where the next drawn item in the thread starts to paint; null when nothing follows. */
  nextTop: number | null;
  /** The message's lines of words, any order (each one fragment of text on one line). */
  lines: readonly Box[];
  /** Everything the message draws: its words and its painted boxes (bubble, code, picture). */
  drawn: readonly Box[];
  /** The horizontal span the row needs beside the words, clear room included. Null when the
   *  column has no such span (a bubble that leaves too little room on its left). */
  slot: { left: number; right: number } | null;
};

/** `top` is where the beside row starts, in the same coordinates as the geometry. */
export type HoverRowPlace = { kind: "below" } | { kind: "beside"; top: number };

export function placeHoverRow(geometry: HoverRowGeometry): HoverRowPlace | null {
  const { messageBottom, nextTop, lines, drawn, slot } = geometry;
  const below = HOVER_ROW_OFFSET_PIXELS + HOVER_ROW_HEIGHT_PIXELS + HOVER_ROW_CLEARANCE_PIXELS;
  if (nextTop === null || nextTop - messageBottom >= below) return { kind: "below" };
  if (slot === null) return null;
  const centres = [...new Set(lines.map(line => Math.round((line.top + line.bottom) / 2)))].sort((a, b) => b - a);
  for (const centre of centres) {
    const row: Box = { left: slot.left, right: slot.right, top: centre - HOVER_ROW_HEIGHT_PIXELS / 2, bottom: centre + HOVER_ROW_HEIGHT_PIXELS / 2 };
    if (nextTop - row.bottom < HOVER_ROW_CLEARANCE_PIXELS) continue;
    if (drawn.some(box => box.left < row.right && box.right > row.left && box.top < row.bottom && box.bottom > row.top)) continue;
    return { kind: "beside", top: row.top };
  }
  return null;
}
