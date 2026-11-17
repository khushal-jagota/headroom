/** Scripted lease exchange through the shared wire codec; no connection or provider starts. */
import { decode_json, encode_bounded } from '../../protocol.ts';
import { MacLeaseLoop, MacPresence } from '../transport/presence.ts';
import type { MacLeaseOptions } from '../transport/presence.ts';
import type { WorkerCleanupUncertainty } from '../../wire.generated.ts';
import { Deferred } from '../../../core/deferred.ts';
import assert from 'node:assert/strict';
import { setImmediate as yield_turn } from 'node:timers/promises';
import type { WorkspaceState } from '../../wire.generated.ts';
export async function leases(input: {
  identity: string;
  replies: string[];
  working: string;
  cleanup_uncertainties: WorkerCleanupUncertainty[];
  legacy_cleanup_notice: string | null;
  workspace_root: string;
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
  const refresh_entered = new Deferred<void>(),
    refresh_release = new Deferred<void>();
  const cleanup_entered = new Deferred<void>(),
    cleanup_release = new Deferred<void>();
  const workspace_calls: string[][] = [],
    cleaned: unknown[] = [],
    facts: boolean[] = [];
  let refreshes = 0;
  const accepted_reply = input.replies[0]!;
  const options: MacLeaseOptions = {
    presence,
    workspace_root: input.workspace_root,
    client: {
      async lease(request) {
        calls.push(Buffer.from(encode_bounded(request, 'LeaseRequest')).toString());
        cursor++;
        const reply = input.replies.shift()!;
        return calls.length <= 2
          ? decode_json(reply, 'LeaseReply')
          : decode_json(reply, 'LeaseRefused');
      },
      async workspace_states(ids) {
        workspace_calls.push([...ids]);
        return {
          states: ids.map((ticket_id) => ({ ticket_id, ended_at: null, deleted: false })),
          at_vps_ms: workspace_calls.length === 1 ? 1700000001999 : 1700000002999,
        };
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
      cleanup_uncertainties: input.cleanup_uncertainties,
      legacy_cleanup_notice: input.legacy_cleanup_notice,
      get admission_stopped() {
        return stopped;
      },
      working_turns: () => [decode_json(input.working, 'WorkingTurn')],
      async refresh_worker_cleanup() {
        refreshes++;
        refresh_entered.resolve();
        await refresh_release.promise;
      },
      async cleanup_workspaces(states, now) {
        cleaned.push({ states: [...states], now });
        cleanup_entered.resolve();
        await cleanup_release.promise;
      },
      seal() {
        stopped = true;
      },
    },
    updates: {
      offer(update) {
        offers.push(Buffer.from(encode_bounded(update, 'MacUpdateCheck')).toString());
      },
    },
    replaced() {
      replaced = true;
    },
    on_failure() {},
    warn() {},
  };
  const loop = new MacLeaseLoop(options);
  const keep_running = [await loop.lease_once()];
  await refresh_entered.promise;
  keep_running.push(await loop.lease_once());
  facts.push(refreshes === 1 && cleaned.length === 0);
  refresh_release.resolve();
  await cleanup_entered.promise;
  keep_running.push(await loop.lease_once());
  facts.push(cleaned.length === 1);
  cleanup_release.resolve();
  await yield_turn();
  keep_running.push(await loop.lease_once());
  await loop.stop({ deadline: performance.now() / 1000 + 5 });
  await loop.settled();
  const { last_lease_failure, ...status } = presence.status(stopped);
  const stop_cleanup_entered = new Deferred<void>(),
    stop_cleanup_release = new Deferred<void>();
  const stop_http_entered = new Deferred<void>();
  let stop_leases = 0;
  const maintenance = {
    ...options,
    journal: {
      async poll_cursor() {
        return cursor;
      },
      async lower_poll_cursor() {
        return true;
      },
    },
    updates: { offer() {} },
  };
  const stopping = new MacLeaseLoop({
    ...maintenance,
    workspace_root: input.workspace_root + '/absent',
    runner: {
      ...options.runner,
      async refresh_worker_cleanup() {},
      async cleanup_workspaces(_states: readonly WorkspaceState[], _now: number) {
        stop_cleanup_entered.resolve();
        await stop_cleanup_release.promise;
      },
    },
    client: {
      async workspace_states(ids) {
        assert.deepEqual(ids, []);
        return { states: [], at_vps_ms: 1700000000000 };
      },
      async lease(_, signal) {
        if (stop_leases++ === 0) return decode_json(accepted_reply, 'LeaseReply');
        stop_http_entered.resolve();
        return new Promise<never>((_, reject) =>
          signal!.addEventListener('abort', () => reject(signal!.reason), { once: true }),
        );
      },
    },
  });
  stopping.start();
  const accepted = stopping.lease_once();
  await stop_cleanup_entered.promise;
  await accepted;
  const http = stopping.lease_once();
  void http.catch(() => {});
  await stop_http_entered.promise;
  let raw_joined = false;
  const stopping_task = stopping.stop({ deadline: performance.now() / 1000 + 5 });
  void stopping_task.catch(() => {});
  void stopping.settled().then(() => {
    raw_joined = true;
  });
  await yield_turn();
  assert.equal(raw_joined, false, 'shutdown retains the actual slow cleanup after HTTP abort');
  stop_cleanup_release.resolve();
  await Promise.all([stopping_task, http.catch(() => {})]);
  await stopping.settled();
  const fault_entered = new Deferred<void>(),
    fault_release = new Deferred<void>();
  const warnings: string[] = [];
  const failing = new MacLeaseLoop({
    ...maintenance,
    client: {
      async lease() {
        return decode_json(accepted_reply, 'LeaseReply');
      },
      async workspace_states(ids) {
        // Final cleanup reads lifecycle first, then refreshes custody. Let that
        // read succeed so the held failure still proves refresh settlement.
        return {
          states: ids.map((ticket_id) => ({ ticket_id, ended_at: null, deleted: false })),
          at_vps_ms: 1700000000000,
        };
      },
    },
    runner: {
      ...options.runner,
      async refresh_worker_cleanup() {
        fault_entered.resolve();
        await fault_release.promise;
        throw Object.assign(new Error('scripted cleanup read failed'), { code: 'EIO' });
      },
    },
    warn: (message) => warnings.push(message),
  });
  await failing.lease_once();
  await fault_entered.promise;
  const failed_stopping = failing.stop({ deadline: performance.now() / 1000 + 5 });
  void failed_stopping.catch(() => {});
  fault_release.resolve();
  await failed_stopping;
  await failing.settled();
  assert.deepEqual(
    warnings,
    ['Ticket workspace cleanup failed: Error'],
    'an ordinary I/O failure remains visible after shutdown aborts its cleanup task',
  );
  return {
    calls,
    offers,
    lowered,
    status,
    failed: last_lease_failure !== null,
    keep_running,
    replaced,
    workspace: { calls: workspace_calls, cleaned, facts, refreshes },
  };
}
