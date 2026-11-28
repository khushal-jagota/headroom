import type { LiveFrame, LiveSink, LiveSubscriptionHandle } from './contracts.ts';

/**
 * Writes the frames of a pull-based feed to a sink until the feed or the subscriber ends. A feed
 * that finishes by itself ends the sink. `close` must wake the feed so that it can finish.
 */
export function pump_frames(
  frames: AsyncIterable<LiveFrame | null>,
  close: () => void,
  sink: LiveSink,
): LiveSubscriptionHandle {
  const abort = new AbortController();
  let stopped = false;
  void (async () => {
    try {
      for await (const frame of frames) {
        if (stopped) break;
        if (!sink.write(frame)) await sink.drain(abort.signal);
      }
    } catch (error) {
      if (!stopped) console.error('live subscription failed', error);
    }
    if (!stopped) sink.end();
  })();
  return {
    close() {
      if (stopped) return;
      stopped = true;
      abort.abort();
      close();
    },
  };
}
