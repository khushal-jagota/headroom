// Health can change without a database write; mounted machine surfaces poll the read.
export const MACHINE_HEALTH_POLL_MS = 5000;

export const MACHINE_ACTIVITY_UNKNOWN = "Current activity on this machine is unknown. Last progress remains available.";

// The browser change signal can be held while the server pauses, so an active move polls its read.
export const SERVER_MOVE_POLL_MS = 3000;
// The new server's port on the target machine when the owner does not choose another.
export const SERVER_MOVE_DEFAULT_PORT = 8767;

// Work moves expose phase changes even while the change stream reconnects.
export const WORK_MOVE_POLL_MS = 1000;
