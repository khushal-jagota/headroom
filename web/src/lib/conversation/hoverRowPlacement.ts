/** Where a message's hover row (its time and copy button) stands (owner's review, B6, B7,
 *  B9). The row needs a 24 px slot 4 px under what it hangs from, inside the gap that already
 *  follows. It never adds space and never covers anything.
 *
 *  - The gap under the message holds the slot (a turn's 32): the row stands there.
 *  - A system line stands closer (the end-of-turn line, the live "Working" line): the row
 *    goes under that line, as it goes under a caption inside the message.
 *  - Anything else stands closer (prose or a tool row in the same turn): no row. */

export const HOVER_ROW_REACH_PIXELS = 4 + 24;

/** What follows the message in the thread, top to bottom, measured. */
export type FollowingItem = { top: number; bottom: number; isSystemLine: boolean };

/** The row's place: how far under the message's bottom edge its slot starts, and how many
 *  of the following items (system lines) it reaches past. Null draws no row. */
export type HoverRowPlace = { offset: number; passes: number };

export function placeHoverRow(messageBottom: number, following: readonly FollowingItem[]): HoverRowPlace | null {
  let edge = messageBottom;
  for (let passes = 0; passes <= following.length; passes += 1) {
    const next = following[passes];
    if (next === undefined || next.top - edge >= HOVER_ROW_REACH_PIXELS) return { offset: edge - messageBottom, passes };
    if (!next.isSystemLine) return null;
    edge = next.bottom;
  }
  return null;
}
