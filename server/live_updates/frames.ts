import { once } from 'node:events';
import type { ServerResponse } from 'node:http';
import { STREAM_RETRY_FRAME } from '../core/http/live_streams/configuration.ts';
import type { LiveFrame, LiveSink, LiveSubscriptionHandle } from './contracts.ts';

export const sse_text = (frame: LiveFrame): string =>
  `event: ${frame.event}\ndata: ${frame.data}\n\n`;

/** The old event stream: today's text, with the retry line before the first frame. */
export class SseSink implements LiveSink {
  readonly #response: ServerResponse;
  #first = true;
  constructor(response: ServerResponse) {
    this.#response = response;
  }
  write(frame: LiveFrame | null): boolean {
    const text = frame ? sse_text(frame) : ': heartbeat\n\n';
    const body = this.#first ? STREAM_RETRY_FRAME + text : text;
    this.#first = false;
    return this.#response.write(body);
  }
  async drain(signal: AbortSignal): Promise<void> {
    await once(this.#response, 'drain', { signal });
  }
  end(): void {
    this.#response.end();
  }
  on_closed(callback: () => void): void {
    this.#response.once('close', callback);
  }
}

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
