/** Warn once per excursion above this many open responses for one client. */
export const CLIENT_STREAM_WARNING_THRESHOLD = 100;

/** The first lines of every event stream. A browser reopens a closed stream after this long. */
export const STREAM_RETRY_FRAME = 'retry: 1000\n\n';
