/**
 * File sync. Each Ticket's `artifacts/` folder reaches the home on its own path, apart from
 * every conversation item. One pass compares the folder with the versions the home confirmed,
 * sends each new or changed file once, and removes what the folder no longer has.
 */
import { lstat, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { FileManifest } from '../../wire.generated.ts';
import {
  FILE_MODIFIED_WINDOW_NS,
  OUTBOX_RETRY_INITIAL_SECONDS,
  OUTBOX_RETRY_MAX_SECONDS,
} from '../configuration.ts';
import { contained_path, error_code } from '../file_metadata.ts';
import type { FilesService } from '../files.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { ExecutorHttpError } from './errors.ts';
import type { MacFileTransport } from './files.ts';

/** The one folder of a Ticket that transfers. The worktree and all other files stay local. */
const ARTIFACTS = 'artifacts';

export interface TicketFileSyncOptions {
  readonly store: Pick<
    OutboxStore,
    'home_files' | 'record_home_file' | 'forget_home_file' | 'pending_events'
  >;
  readonly files: Pick<FilesService, 'file_digest'>;
  readonly transfer: Pick<MacFileTransport, 'upload_ticket_file' | 'remove_ticket_file'>;
  /** The folder that holds one folder per Ticket. */
  readonly tickets_root: string;
  readonly now_ns: () => bigint;
  readonly warn: (message: string) => void;
}
interface FoundFile {
  readonly relative_path: string;
  readonly source: string;
  readonly byte_count: number;
  readonly modified_ns: bigint;
}
function describe(error: unknown): string {
  if (error instanceof ExecutorHttpError)
    return `HTTP ${error.status} ${error.code}: ${error.message}`;
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}
/**
 * The home answered that it will never take this request as sent. A home from before the
 * file sync answers 404, and 408, 409 and 429 pass with time, so those retry.
 */
function refused(error: unknown): boolean {
  return (
    error instanceof ExecutorHttpError &&
    error.status >= 400 &&
    error.status < 500 &&
    ![404, 408, 409, 429].includes(error.status)
  );
}
const absent = (error: unknown) => ['ENOENT', 'ENOTDIR'].includes(error_code(error) ?? '');
const unreadable = (error: unknown) =>
  ['EACCES', 'EPERM', 'ELOOP'].includes(error_code(error) ?? '');

export class MacTicketFileSync {
  private readonly options: TicketFileSyncOptions;
  private readonly cancellation = new AbortController();
  /** A present Ticket has a running pass. True means that another pass must follow it. */
  private readonly again = new Map<string, boolean>();
  private readonly passes = new Set<Promise<void>>();
  /** File versions that the home refused. They are not hashed or sent again until they change. */
  private readonly refusals = new Map<string, string>();
  private startup: Promise<void> | undefined;
  private closed = false;
  readonly request = (ticket_id: string): void => {
    if (this.closed) return;
    if (this.again.has(ticket_id)) {
      this.again.set(ticket_id, true);
      return;
    }
    this.again.set(ticket_id, false);
    const pass = this.run(ticket_id).finally(() => this.passes.delete(pass));
    this.passes.add(pass);
  };
  constructor(options: TicketFileSyncOptions) {
    this.options = options;
  }
  /** A restart can interrupt a sync, so every Ticket folder gets one pass at start. */
  start(): void {
    if (this.closed || this.startup) return;
    this.options.store.pending_events.on('ticket_activity', this.request);
    this.startup = (async () => {
      let names: string[];
      try {
        names = await readdir(this.options.tickets_root);
      } catch (error) {
        if (!absent(error)) this.options.warn(`Ticket file sync cannot list: ${describe(error)}`);
        return;
      }
      for (const name of names) if (/^[A-Za-z0-9_-]+$/.test(name)) this.request(name);
    })();
  }
  private async run(ticket_id: string): Promise<void> {
    const signal = this.cancellation.signal;
    let delay = OUTBOX_RETRY_INITIAL_SECONDS;
    try {
      while (!this.closed) {
        this.again.set(ticket_id, false);
        let complete = false;
        try {
          complete = await this.pass(ticket_id, signal);
        } catch (error) {
          if (this.closed) return;
          this.options.warn(
            `Ticket file sync failed ticket=${ticket_id} retry_in=${delay}s: ${describe(error)}`,
          );
        }
        if (complete) {
          if (!this.again.get(ticket_id)) return;
          delay = OUTBOX_RETRY_INITIAL_SECONDS;
          continue;
        }
        if (this.closed) return;
        await new Promise<void>((resolve) => {
          const timer = setTimeout(done, delay * 1000);
          function done() {
            clearTimeout(timer);
            signal.removeEventListener('abort', done);
            resolve();
          }
          signal.addEventListener('abort', done, { once: true });
        });
        delay = Math.min(delay * 2, OUTBOX_RETRY_MAX_SECONDS);
      }
    } finally {
      // Leave in the same step as the decision to end, so a request cannot join a dead pass.
      this.again.delete(ticket_id);
    }
  }
  /** Lists the regular files. `whole` is false when a folder was absent or unreadable. */
  private async scan(ticket_id: string): Promise<{ found: FoundFile[]; whole: boolean }> {
    const root = join(this.options.tickets_root, ticket_id);
    const found: FoundFile[] = [];
    let whole = true;
    const folders = [ARTIFACTS];
    while (folders.length) {
      const folder = folders.pop()!;
      let names: string[];
      try {
        names = (await readdir(contained_path(root, folder))).sort();
      } catch (error) {
        if (!absent(error) && !unreadable(error) && error_code(error) !== undefined) throw error;
        whole = false;
        continue;
      }
      for (const name of names) {
        const relative_path = `${folder}/${name}`;
        try {
          const source = contained_path(root, relative_path);
          const info = await lstat(source, { bigint: true });
          if (info.isDirectory()) folders.push(relative_path);
          else if (info.isFile())
            found.push({
              relative_path,
              source,
              byte_count: Number(info.size),
              modified_ns: info.mtimeNs,
            });
        } catch (error) {
          // A symlink or an unsafe name never transfers. A vanished entry is seen next pass.
          if (!absent(error) && !unreadable(error) && error_code(error) !== undefined) throw error;
          // An entry that cannot be read is not proof that its file was deleted.
          if (unreadable(error)) whole = false;
        }
      }
    }
    return { found, whole };
  }
  /** Returns false when a file changed under the pass, so another pass must follow. */
  private async pass(ticket_id: string, signal: AbortSignal): Promise<boolean> {
    const { store, files, transfer, warn } = this.options;
    const checked_ns = this.options.now_ns();
    const { found, whole } = await this.scan(ticket_id);
    const held = new Map(
      store
        .home_files(ticket_id)
        .filter((row) => row.relative_path.startsWith(`${ARTIFACTS}/`))
        .map((row) => [row.relative_path, row]),
    );
    let settled = true;
    if (whole) {
      const present = new Set(found.map((file) => file.relative_path));
      for (const row of held.values()) {
        if (present.has(row.relative_path)) continue;
        signal.throwIfAborted();
        try {
          await transfer.remove_ticket_file(
            {
              scope: 'ticket',
              owner_id: ticket_id,
              relative_path: row.relative_path,
              sha256: row.sha256,
              byte_count: row.byte_count,
            },
            signal,
          );
        } catch (error) {
          if (!refused(error)) throw error;
          warn(`Home refused removal ticket=${ticket_id} ${row.relative_path}: ${describe(error)}`);
          continue;
        }
        store.forget_home_file(ticket_id, row.relative_path);
      }
    }
    for (const file of found) {
      const row = held.get(file.relative_path);
      if (row?.byte_count === file.byte_count && row.modified_ns === file.modified_ns) continue;
      const refusal = `${ticket_id}/${file.relative_path}`,
        version = `${file.byte_count}:${file.modified_ns}`;
      if (this.refusals.get(refusal) === version) continue;
      signal.throwIfAborted();
      try {
        const [sha256, byte_count] = await files.file_digest(file.source);
        if (row?.sha256 !== sha256 || row.byte_count !== byte_count) {
          const manifest: FileManifest = {
            scope: 'ticket',
            owner_id: ticket_id,
            relative_path: file.relative_path,
            sha256,
            byte_count,
          };
          await transfer.upload_ticket_file(manifest, file.source, signal);
        }
        store.record_home_file({
          ticket_id,
          relative_path: file.relative_path,
          sha256,
          byte_count,
          // A file written this recently can change again without a new timestamp.
          modified_ns:
            checked_ns - file.modified_ns <= BigInt(FILE_MODIFIED_WINDOW_NS)
              ? -1n
              : file.modified_ns,
        });
      } catch (error) {
        if (signal.aborted) throw error;
        warn(`File not sent ticket=${ticket_id} ${file.relative_path}: ${describe(error)}`);
        // A refused or unreadable file waits for a change. Any other failure retries this
        // file in the next pass, and the files after it still go now.
        if (refused(error) || unreadable(error)) this.refusals.set(refusal, version);
        else settled = false;
      }
    }
    return settled;
  }
  stop(options: StopOptions): Promise<void> {
    this.closed = true;
    this.options.store.pending_events.off('ticket_activity', this.request);
    this.cancellation.abort();
    return observe_shutdown(
      this.settled(),
      options,
      'Ticket file sync remains unfinished at the shutdown deadline.',
    );
  }
  async settled(): Promise<void> {
    await this.startup;
    while (this.passes.size) await Promise.allSettled([...this.passes]);
  }
}
