/** Shared synthetic native setup and real-store custody for the Mac direct proofs. */
import { mkdirSync } from 'node:fs';
import { stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { ConversationMessageFiles } from '../../../conversation/message_files.ts';
import { encode_value } from '../../protocol.ts';
import type { MailboxCommand } from '../../wire.generated.ts';
import type { MacNativeChildrenOptions } from '../runner/contracts.ts';
import { OutboxStore } from '../storage/outbox.ts';
import { open_command_journal } from '../storage/journal.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import { read_canonical_body } from '../mailbox/poll.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';

export function gate() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

export function native_fixture_options(
  input: Pick<
    MacNativeChildrenOptions,
    'paths' | 'workspace_home' | 'factory' | 'on_failure' | 'native_skill_home'
  > & { snapshot_directory: string },
): Omit<MacNativeChildrenOptions, 'event_sink'> {
  return {
    paths: input.paths,
    workspace_home: input.workspace_home,
    factory: input.factory,
    on_failure: input.on_failure,
    native_skill_home: input.native_skill_home,
    message_files: null as unknown as ConversationMessageFiles,
    files: {
      async open_directory(path) {
        mkdirSync(path, { recursive: true });
        return { id: 0 };
      },
      async close_directory() {},
    },
    skills: {
      async install_native_skill_snapshot() {
        return input.snapshot_directory;
      },
      async stop() {},
      async settled() {},
    },
    record_children: async () => {},
  };
}

export interface FixtureStores {
  store: OutboxStore;
  journal: AsyncCommandJournal;
}
type Cleanup = Pick<AsyncCommandJournal, 'stop' | 'settled'>;
async function close_owners(
  owners: readonly Cleanup[],
  options: StopOptions,
  bounded_startup = false,
): Promise<void> {
  const errors: unknown[] = [];
  // The journal is first: its actual sidecar must join before the spool parent closes.
  for (const owner of owners) {
    let actually_joined = false;
    const actual = Promise.resolve()
      .then(() => owner.settled())
      .finally(() => {
        actually_joined = true;
      });
    const joined = await Promise.allSettled([
      Promise.resolve().then(() => owner.stop(options)),
      bounded_startup
        ? observe_shutdown(actual, options, 'Fixture startup cleanup remains unfinished.')
        : actual,
    ]);
    for (const result of joined) if (result.status === 'rejected') errors.push(result.reason);
    // A retained journal may need another stop attempt. Keep its parent open for that retry.
    if (!actually_joined) break;
  }
  if (errors.length) throw new AggregateError(errors, 'Fixture resource cleanup failed.');
}

export async function open_fixture_stores(
  spool_root: string,
  now_ms: bigint,
  on_failure: (error: Error) => void,
  options: StopOptions,
): Promise<FixtureStores> {
  const owners: Cleanup[] = [];
  try {
    const store = await OutboxStore.open(
      spool_root,
      {
        monotonic: () => 100,
        now_ns: () => now_ms * 1_000_000n,
      },
      options,
      on_failure,
    );
    owners.push(store);
    const journal = await open_command_journal(
      store,
      'inc',
      {
        outputs_module: new URL('../runner/logic/outcomes.ts', import.meta.url),
        now_ms: () => now_ms,
        on_failure,
      },
      options,
    );
    return { store, journal };
  } catch (error) {
    const carried = (error as { cleanup?: Cleanup } | null)?.cleanup;
    if (carried && !owners.includes(carried)) owners.unshift(carried);
    const cleanup: Cleanup = {
      stop: (next) => close_owners(owners, next, true),
      async settled() {
        const joined = await Promise.allSettled(owners.map((owner) => owner.settled()));
        const errors = joined.flatMap((result) =>
          result.status === 'rejected' ? [result.reason] : [],
        );
        if (errors.length) throw new AggregateError(errors, 'Fixture startup cleanup failed.');
      },
    };
    try {
      await cleanup.stop(options);
    } catch (failure) {
      throw Object.assign(new AggregateError([error, failure], 'Fixture stores did not open.'), {
        cleanup,
      });
    }
    throw error;
  }
}

export function close_fixture_stores(stores: FixtureStores, options: StopOptions): Promise<void> {
  return close_owners([stores.journal, stores.store], options);
}

export async function record_commands(
  journal: AsyncCommandJournal,
  commands: readonly MailboxCommand[],
): Promise<void> {
  const cursor = await journal.record_poll_reply(
    stringify_pydantic_json(encode_value({ commands }, 'MailboxPollReply')),
    'inc',
    0n,
  );
  try {
    for await (const row of cursor) await read_canonical_body(row.command);
  } finally {
    await cursor.close();
  }
}
