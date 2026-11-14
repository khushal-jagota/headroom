/** Scripted lease exchange through the shared wire codec; no connection or provider starts. */
import { decode_json, encode_bounded } from '../../protocol.ts';
import { MacLeaseLoop, MacPresence } from '../transport/presence.ts';
import type { MacLeaseOptions } from '../transport/presence.ts';
export async function leases(input: {
  identity: string;
  replies: string[];
  working: string;
}): Promise<unknown> {
  const identity = decode_json(input.identity, 'ExecutorIdentity');
  const presence = new MacPresence(identity, () => 1700000000000, 7);
  const calls: string[] = [],
    offers: string[] = [],
    lowered: number[][] = [];
  const waiting = Array.from(
    { length: 65 },
    (_, index) => [`conversation-${index}`, Number(index + 1)] as const,
  );
  let cursor = 100,
    replaced = false,
    stopped = false;
  const options: MacLeaseOptions = {
    presence,
    client: {
      async lease(request) {
        calls.push(Buffer.from(encode_bounded(request, 'LeaseRequest')).toString());
        cursor++;
        const reply = input.replies.shift()!;
        return calls.length === 1
          ? decode_json(reply, 'LeaseReply')
          : decode_json(reply, 'LeaseRefused');
      },
    },
    outbox: {
      pending_count: () => 7,
      oldest_age_ms: () => 11,
      pending_conversations: () => waiting,
    },
    journal: {
      async poll_cursor() {
        return cursor;
      },
      async lower_poll_cursor(high, before) {
        lowered.push([Number(high), Number(before)]);
        return true;
      },
    },
    runner: {
      get admission_stopped() {
        return stopped;
      },
      working_turns: () => [decode_json(input.working, 'WorkingTurn')],
      seal() {
        stopped = true;
      },
    },
    updates: {
      offer(update) {
        offers.push(Buffer.from(encode_bounded(update, 'MacUpdateCheck')).toString());
      },
    },
    async record_children() {},
    replaced() {
      replaced = true;
    },
    on_failure() {},
    warn() {},
  };
  const loop = new MacLeaseLoop(options);
  const keep_running: boolean[] = [];
  for (let index = 0; index < 3; index++) {
    keep_running.push(await loop.lease_once());
  }
  const { last_lease_failure, ...status } = presence.status(stopped);
  const stopping = new MacLeaseLoop({
    ...options,
    client: {
      async lease(_, signal) {
        return new Promise<never>((_, reject) =>
          signal!.addEventListener('abort', () => reject(signal!.reason), { once: true }),
        );
      },
    },
  });
  stopping.start();
  await Promise.resolve();
  await stopping.stop({ deadline: performance.now() / 1000 + 5 });
  await stopping.settled();
  return {
    calls,
    offers,
    lowered,
    status,
    failed: last_lease_failure !== null,
    keep_running,
    replaced,
  };
}
