/** Public journal/real-codec comparison. Python supplies inputs and only the output-policy oracle. */
import { createHash } from 'node:crypto';
import { OutboxStore } from '../outbox.ts';
import { open_command_journal } from '../journal.ts';
import type { CanonicalReader, AsyncCommandJournal, JournalCursor } from '../journal_contracts.ts';
import type { ItemColumns } from '../contracts.ts';
interface Step {
  op:
    | 'poll'
    | 'finish'
    | 'lower'
    | 'published'
    | 'reported'
    | 'shrink'
    | 'inspect'
    | 'publish'
    | 'crash';
  hex?: string;
  id?: string;
  ids?: string[];
  incarnation?: string;
  after?: string;
  high?: string;
  at_lease?: string;
  owed?: boolean;
  columns?: ItemColumns;
  payload_hex?: string;
  expected_command_count?: number;
}
interface Input {
  root: string;
  fixture: string;
  incarnation: string;
  now_ms: string;
  steps: Step[];
  gate?: string;
  gate_command_id?: string;
}
interface StreamStats {
  max_chunk_bytes: number;
  streamed_bytes: number;
  readers: number;
}
async function digest(reader: CanonicalReader | null, stats: StreamStats): Promise<unknown> {
  if (reader === null) return null;
  const hash = createHash('sha256');
  let bytes = 0;
  try {
    for await (const part of reader) {
      if (part.byteLength > 256 * 1024) throw new Error('Journal chunk exceeds 256 KiB');
      stats.max_chunk_bytes = Math.max(stats.max_chunk_bytes, part.byteLength);
      hash.update(part);
      bytes += part.byteLength;
    }
  } finally {
    await reader.close();
  }
  if (bytes !== reader.byte_count)
    throw new Error('Journal reader byte_count differs from its stream');
  stats.readers++;
  stats.streamed_bytes += bytes;
  return [bytes, hash.digest('hex')];
}
async function inventory<
  T extends { command_id: string; command?: CanonicalReader; result?: CanonicalReader },
>(cursor: JournalCursor<T>, stats: StreamStats): Promise<unknown[]> {
  const rows: unknown[] = [];
  try {
    for await (const row of cursor)
      rows.push({
        id: row.command_id,
        ...(row.command ? { command: await digest(row.command, stats) } : {}),
        ...(row.result ? { result: await digest(row.result, stats) } : {}),
      });
  } finally {
    await cursor.close();
  }
  return rows;
}
async function inspect(
  journal: AsyncCommandJournal,
  ids: string[],
  stats: StreamStats,
): Promise<unknown> {
  return {
    cursor: String(await journal.poll_cursor()),
    unpublished: await inventory(await journal.unpublished(), stats),
    unreported: await inventory(await journal.unreported(32), stats),
    results: await Promise.all(
      ids.map(async (id) => [id, await digest(await journal.result_of(id), stats)]),
    ),
  };
}
export async function journal_parity(input: Input): Promise<unknown> {
  const options = { deadline: performance.now() / 1000 + 25 };
  let owner: OutboxStore | undefined, fault: Error | undefined;
  const on_failure = (error: Error) => {
    fault ??= error;
    if (owner) void owner.stop(options).catch(() => {});
  };
  const store = await OutboxStore.open(
    input.root,
    {
      monotonic: () => 100,
      now_ns: () => BigInt(input.now_ms) * 1_000_000n,
    },
    options,
    on_failure,
  );
  owner = store;
  if (fault) on_failure(fault);
  const observations: unknown[] = [];
  const stream: StreamStats = { max_chunk_bytes: 0, streamed_bytes: 0, readers: 0 };
  const started = performance.now(),
    interval_ms = 10,
    lateness: number[] = [];
  let next = started + interval_ms,
    max_rss_bytes = process.memoryUsage().rss;
  const timer = setInterval(() => {
    const now = performance.now();
    lateness.push(Math.max(0, now - next));
    next = now + interval_ms;
    max_rss_bytes = Math.max(max_rss_bytes, process.memoryUsage().rss);
  }, interval_ms);
  try {
    if (fault) throw fault;
    const fixture = new URL('./journal_fixture.ts', import.meta.url);
    fixture.searchParams.set('fixture', input.fixture);
    if (input.gate) {
      fixture.searchParams.set('gate', input.gate);
      fixture.searchParams.set('gate_command_id', input.gate_command_id!);
    }
    const journal = await open_command_journal(
      store,
      input.incarnation,
      {
        outputs_module: fixture,
        now_ms: () => BigInt(input.now_ms),
        on_failure,
      },
      options,
    );
    for (const step of input.steps) {
      switch (step.op) {
        case 'poll': {
          const cursor = await journal.record_poll_reply(
            Buffer.from(step.hex!, 'hex'),
            step.incarnation ?? input.incarnation,
            BigInt(step.after ?? '0'),
          );
          if (cursor.command_count !== step.expected_command_count)
            throw new Error('Fresh cursor lost the original poll reply command count');
          observations.push(await inventory(cursor, stream));
          break;
        }
        case 'finish':
          await journal.finish(step.id!, Buffer.from(step.hex!, 'hex'), step.owed!);
          break;
        case 'lower':
          observations.push(
            await journal.lower_poll_cursor(BigInt(step.high!), BigInt(step.at_lease!)),
          );
          break;
        case 'published':
          await journal.mark_outputs_published(step.id!);
          break;
        case 'reported':
          await journal.mark_reported(step.ids!);
          break;
        case 'shrink':
          await journal.shrink_to_tombstones();
          break;
        case 'inspect':
          observations.push({
            ...((await inspect(journal, step.ids!, stream)) as object),
            pending: store.pending().map((row) => [row.submission_id, String(row.sequence)]),
          });
          break;
        case 'publish':
          store.append(step.columns!, Buffer.from(step.payload_hex!, 'hex'), [], [], () => {});
          break;
        // The parent directly joins this process. Both committed stores survive actual process exit.
        case 'crash':
          process.exit(73);
      }
    }
  } finally {
    // The outbox owns journal shutdown, then DB closure, file-worker join and the process lock.
    try {
      try {
        await store.stop(options);
      } finally {
        await store.settled();
      }
    } finally {
      // Let an already-due timer observe the final synchronous close before clearing it.
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
      clearInterval(timer);
      max_rss_bytes = Math.max(max_rss_bytes, process.memoryUsage().rss);
    }
  }
  if (fault) throw fault;
  lateness.sort((a, b) => a - b);
  return {
    observations,
    stats: {
      ...stream,
      interval_ms,
      samples: lateness.length,
      max_lateness_ms: lateness.at(-1) ?? 0,
      p99_lateness_ms: lateness[Math.ceil(lateness.length * 0.99) - 1] ?? 0,
      elapsed_seconds: (performance.now() - started) / 1000,
      max_rss_bytes,
    },
  };
}
