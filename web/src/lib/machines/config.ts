// Health can change without a database write; mounted machine surfaces poll the read.
export const MACHINE_HEALTH_POLL_MS = 5000;

export const MACHINE_ACTIVITY_UNKNOWN = "Current activity on Mac is unknown. Last progress remains available.";
