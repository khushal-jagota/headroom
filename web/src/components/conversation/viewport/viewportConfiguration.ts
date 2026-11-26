/** How far from the newest line still counts as being with it. */
export const NEAR_NEWEST_LINE_PIXELS = 48;

/** Where a message you have just sent comes to rest: clear of the fade at the top. */
export const SENT_MESSAGE_TOP_GAP_PIXELS = 32;

/** What the newest line keeps under it: the transcript's bottom edge (v17 (8): at least 32px
 *  above the composer or ask card), which is also the room a message's hover time and Copy
 *  hang in, so they are never clipped. The thread's bottom padding (ConversationViewport) matches. */
export const NEWEST_LINE_BOTTOM_GAP_PIXELS = 32;

/** The approved artifact advances complete reply lines faster than ordinary layout moves. */
export const REPLY_LINE_MOVE_MS = 120;

/** A saved text address cannot hide an open conversation beyond this active wait. */
export const TEXT_RESTORATION_WAIT_MS = 5_000;

/** An open stays hidden while its visible tail finishes presenting, but never beyond this
 *  wait: a body that never finishes is revealed at the newest line and placed again when it does. */
export const ENTRY_PRESENTATION_WAIT_MS = 2_500;

/** The way back to the newest line: its words ("Jump to latest", "3 new messages") and the
 *  action, which takes the button pressed so focus can return to the thread after. */
export type ConversationJump = { label: string; go: (button: HTMLButtonElement | null) => void };
