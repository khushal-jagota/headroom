// Health can change without a database write; mounted machine surfaces poll the read.
export const MACHINE_HEALTH_POLL_MS = 5000;

// The browser change signal can be held while the server pauses, so an active move polls its read.
export const SERVER_MOVE_POLL_MS = 3000;
// The new server's port on the target machine when the owner does not choose another.
export const SERVER_MOVE_DEFAULT_PORT = 8767;

// Work moves expose phase changes even while the change stream reconnects.
export const WORK_MOVE_POLL_MS = 1000;

// The move dialog shows tenths of a second, so its clock ticks faster than the server poll.
export const WORK_MOVE_TICK_MS = 100;
// A finished move stays on the page button this long, then the button and dialog return to idle.
export const WORK_MOVE_SETTLE_MS = 4000;
