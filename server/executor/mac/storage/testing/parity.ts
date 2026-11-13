/** Synthetic export-only driver. Python supplies opaque bytes; snapshots prove their custody. */
import { mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setImmediate as yield_turn } from 'node:timers/promises';
import { register_test_root } from '../../../../core/test_paths.ts';
import type { Database } from '../../../../core/database/contracts.ts';
import { OutboxStore } from '../outbox.ts';
import type { CaptureRows, ItemColumns, TicketFileKey } from '../contracts.ts';
interface Step {
  operation: 'append' | 'acknowledge' | 'recent' | 'orphan' | 'crash';
  columns?: ItemColumns;
  payload_hex?: string;
  capture?: boolean;
  reject?: boolean;
  submission_id?: string;
  receipt_hex?: string;
  accepted?: boolean;
  invalidated?: TicketFileKey[];
}
interface Input {
  root: string;
  opened_at: number;
  observed_at: number;
  now_ns: string;
  steps: Step[];
}
const pragmas =
  'user_version journal_mode synchronous fullfsync trusted_schema foreign_keys recursive_triggers auto_vacuum'.split(
    ' ',
  );
function profile(database: Database, names: string[], writer: boolean) {
  const sql = `SELECT ${names.map((name) => `(SELECT ${name} FROM pragma_${name}) AS ${name}`).join(',')}`;
  return writer ? database.write((tx) => tx.get(sql)) : database.read((tx) => tx.get(sql));
}
async function open_store(...args: Parameters<typeof OutboxStore.open>): Promise<OutboxStore> {
  try {
    return await OutboxStore.open(...args);
  } catch (error) {
    const cleanup = (error as { cleanup?: Pick<OutboxStore, 'stop' | 'settled'> }).cleanup;
    if (cleanup) {
      try {
        await cleanup.stop(args[2]);
      } finally {
        await cleanup.settled();
      }
    }
    throw error;
  }
}
export async function spool_parity(input: Input): Promise<unknown> {
  let monotonic = input.opened_at;
  const options = { deadline: performance.now() / 1000 + 10 };
  const store = await open_store(
    join(input.root, 'spool'),
    {
      monotonic: () => monotonic,
      now_ns: () => BigInt(input.now_ns),
    },
    options,
    () => {},
  );
  const captures: unknown[] = [],
    receipts: unknown[] = [],
    refusals: string[] = [];
  try {
    for (const step of input.steps) {
      if (step.operation === 'recent' || step.operation === 'orphan') {
        writeFileSync(
          join(
            input.root,
            step.operation === 'recent'
              ? 'artifacts/tickets/ticket/recent.txt'
              : 'spool/blobs/.tmp-orphan',
          ),
          step.operation === 'recent' ? 'recent-B\n' : 'uncommitted',
        );
      } else if (step.operation === 'acknowledge') {
        await store.acknowledge(
          step.submission_id!,
          store.sender_id,
          Buffer.from(step.receipt_hex!, 'hex'),
          step.accepted!,
          step.invalidated ?? [],
          () => {},
        );
        receipts.push(store.lookup(step.submission_id!)?.receipt_json?.toString('hex') ?? null);
      } else {
        const append = (capture: CaptureRows = { files: [], updates: [] }) =>
          store.append(
            step.columns!,
            Buffer.from(step.payload_hex!, 'hex'),
            capture.files,
            capture.updates,
            () => {
              if (step.operation === 'crash') process.exit(71);
            },
          );
        if (step.reject) {
          try {
            append();
            refusals.push('NOT REFUSED');
          } catch (error) {
            refusals.push((error as Error).message);
          }
        } else if (step.capture) {
          await store.with_capture('ticket', (capture) => {
            append(capture);
            captures.push(capture.files);
          });
        } else append();
      }
    }
    monotonic = input.observed_at;
    // Test-only access to the guarded capability proves both live connection profiles.
    const database = (store as unknown as { database: Database }).database;
    return {
      sender: store.sender_id,
      captures,
      receipts,
      ...(refusals.length ? { refusals } : {}),
      pending: store.pending().map((row) => [row.submission_id, row.sequence]),
      conversations: store.pending_conversations(),
      count: store.pending_count(),
      age: store.oldest_age_ms(),
      writer_profile: profile(database, pragmas, true),
      reader_profile: profile(database, ['trusted_schema', 'foreign_keys'], false),
    };
  } finally {
    await store.stop(options);
    await store.settled();
  }
}

/** Manual disk measurement only: every append and acknowledgement receives its own event-loop turn. */
export async function spool_burst(parent: string): Promise<unknown> {
  const root = realpathSync(mkdtempSync(join(parent, 'mac-spool-burst-'))),
    unregister = register_test_root(root);
  const options = { deadline: performance.now() / 1000 + 60 };
  const store = await open_store(
    join(root, 'spool'),
    {
      monotonic: () => performance.now() / 1000,
      now_ns: () => BigInt(Date.now()) * 1_000_000n,
    },
    options,
    () => {},
  ).catch((error) => {
    unregister();
    throw error;
  });
  const rows = 256,
    interval_ms = 10,
    delays: number[] = [];
  const payload = Buffer.from(
    JSON.stringify({ files: [], payload: { text: 'x'.repeat(1024 * 1024 - 1024) } }),
  );
  let next = performance.now() + interval_ms,
    max_rss = process.memoryUsage().rss,
    max_operation_ms = 0;
  const timer = setInterval(() => {
    const now = performance.now();
    delays.push(Math.max(0, now - next));
    next = now + interval_ms;
  }, interval_ms);
  const started = performance.now();
  try {
    for (let i = 0; i < rows; i++) {
      let before = performance.now();
      const row = store.append(
        {
          submission_id: `burst_${i}`,
          conversation_id: 'burst',
          child_id: '',
          kind: 'backend_event',
        },
        payload,
        [],
        [],
        () => {},
      );
      max_operation_ms = Math.max(max_operation_ms, performance.now() - before);
      await yield_turn();
      before = performance.now();
      await store.acknowledge(
        row.submission_id,
        store.sender_id,
        Buffer.from('{}'),
        true,
        [],
        () => {},
      );
      max_operation_ms = Math.max(max_operation_ms, performance.now() - before);
      max_rss = Math.max(max_rss, process.memoryUsage().rss);
      await yield_turn();
    }
    await new Promise((resolve) => setTimeout(resolve, interval_ms));
    const sorted = delays.sort((a, b) => a - b);
    return {
      disk_root: root,
      rows,
      bytes: rows * payload.length,
      payload_bytes: payload.length,
      elapsed_seconds: (performance.now() - started) / 1000,
      interval_ms,
      timer_ticks: sorted.length,
      max_lateness_ms: sorted.at(-1),
      p99_lateness_ms: sorted[Math.ceil(sorted.length * 0.99) - 1],
      max_operation_ms,
      max_rss_bytes: Math.max(max_rss, process.resourceUsage().maxRSS * 1024),
      real_mac_worker_trigger_ms: 50,
    };
  } finally {
    clearInterval(timer);
    try {
      await store.stop(options);
    } finally {
      await store.settled();
      unregister();
      rmSync(root, { recursive: true, force: true });
    }
  }
}
