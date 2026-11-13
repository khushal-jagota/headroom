/** Machine catalogs travel through the mailbox; SQLite owns their last complete copy. */
import type { Database } from '../core/database/contracts.ts';
import { is_os_error } from '../core/system_errors.ts';
import { stringify_python_json } from '../core/http/numeric_json.ts';
import { python_repr } from './protocol_errors.ts';
import { ConversationBackendKey } from '../conversation/contracts.ts';
import type { BackendSnapshot, SnapshotOptions } from '../conversation/snapshot/contracts.ts';
import type { RemoteCatalogSource } from '../conversation/model_resolution/contracts.ts';
import { observe_shutdown, is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { ServerLifecycleError } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { SqliteCommandMailbox } from './vps_mailbox.ts';
import type { MacPresenceService } from './vps_presence.ts';
import { MODEL_LIST_LIMIT_SECONDS } from './configuration.ts';
import { CommandSettlementReason } from './contracts.ts';
import { as_object, decode_json, ValueError } from './protocol.ts';

export const MAC_AWAY_DIAGNOSIS = 'The Mac is away.';
export interface CatalogRunAuthority {
  owns_conversation(conversation_id: string): boolean;
}
type Presence = Pick<MacPresenceService, 'current_identity' | 'is_present'>;
type Mailbox = Pick<
  SqliteCommandMailbox,
  'put_model_list_request' | 'wait_result' | 'withdraw_waiting'
>;
interface CatalogRead {
  readonly refresh: boolean;
  readonly controller: AbortController;
  readonly promise: Promise<BackendSnapshot>;
}
function aborted(signal: AbortSignal): void {
  if (signal.aborted) throw signal.reason;
}
function cancelled(): DOMException {
  return new DOMException('Executor catalog read cancelled.', 'AbortError');
}
function observe<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(signal.reason ?? cancelled());
  return new Promise<T>((resolve, reject) => {
    const abort = () => {
      signal.removeEventListener('abort', abort);
      reject(signal.reason ?? cancelled());
    };
    signal.addEventListener('abort', abort, { once: true });
    void promise.then(
      (value) => {
        signal.removeEventListener('abort', abort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener('abort', abort);
        reject(error);
      },
    );
  });
}
/** The database, mailbox, and presence service remain owned by the invocation. */
export class VpsCatalogRelay implements RemoteCatalogSource {
  private readonly tasks = new Map<ConversationBackendKey, CatalogRead>();
  private readonly operations = new Set<Promise<BackendSnapshot>>();
  private closed = false;
  private resolve_completion!: () => void;
  private reject_completion!: (error: unknown) => void;
  private readonly completion = new Promise<void>((resolve, reject) => {
    this.resolve_completion = resolve;
    this.reject_completion = reject;
  });
  readonly machine_id: string;
  private readonly database: Database;
  readonly runs: CatalogRunAuthority;
  readonly presence: Presence;
  readonly mailbox: Mailbox;
  constructor(
    database: Database,
    runs: CatalogRunAuthority,
    presence: Presence,
    mailbox: Mailbox,
    options: { machine_id?: string } = {},
  ) {
    this.database = database;
    this.runs = runs;
    this.presence = presence;
    this.mailbox = mailbox;
    this.machine_id = options.machine_id ?? 'macos';
    void this.completion.catch(() => {});
  }
  get identity() {
    return this.presence.current_identity();
  }
  get ready(): boolean {
    return this.presence.is_present();
  }
  owns_conversation(conversation_id: string): boolean {
    return this.runs.owns_conversation(conversation_id);
  }
  private admit(): void {
    if (this.closed) throw new ServerLifecycleError('Executor catalog relay is closed.');
  }
  retained(backend_key: ConversationBackendKey): BackendSnapshot | null {
    this.admit();
    const row = this.database.read((sql) =>
      sql.get<{ snapshot_json: string }>(
        'SELECT snapshot_json FROM machine_backend_snapshots WHERE machine_id=? AND backend_key=?',
        [this.machine_id, backend_key],
      ),
    );
    return row ? this.decode(row.snapshot_json) : null;
  }
  private decode(body: string): BackendSnapshot {
    // Runtime decoding supplies all source defaults and preserves exact wire integers.
    // Existing domain contracts describe model versions as numbers; do not round them here.
    return decode_json(body, 'BackendSnapshot') as unknown as BackendSnapshot;
  }
  async snapshot(
    backend_key: ConversationBackendKey,
    options: SnapshotOptions = {},
  ): Promise<BackendSnapshot> {
    this.admit();
    const { refresh = false, signal } = options;
    if (signal?.aborted) throw signal.reason ?? cancelled();
    const kept = this.retained(backend_key);
    if (!this.presence.is_present()) {
      const answer = this.answer(backend_key, kept);
      return { ...answer, diagnoses: [...answer.diagnoses, MAC_AWAY_DIAGNOSIS] };
    }
    let active = this.tasks.get(backend_key);
    if (active && refresh && !active.refresh) {
      try {
        await observe(active.promise, signal);
      } catch (error) {
        if (!active.controller.signal.aborted) throw error;
        if (signal?.aborted) throw signal.reason ?? cancelled();
      }
      this.admit();
      active = this.tasks.get(backend_key);
    }
    const pending = active ?? this.start_read(backend_key, refresh);
    if (kept !== null && !refresh) return kept;
    try {
      return await observe(pending.promise, signal);
    } catch (error) {
      if (signal?.aborted) throw signal.reason ?? cancelled();
      if (pending.controller.signal.aborted && !this.closed)
        return this.answer(backend_key, this.retained(backend_key));
      throw error;
    }
  }
  private start_read(backend_key: ConversationBackendKey, refresh: boolean): CatalogRead {
    this.admit();
    const controller = new AbortController();
    const promise = this.read(backend_key, refresh, controller.signal);
    const pending = { refresh, controller, promise };
    this.tasks.set(backend_key, pending);
    this.operations.add(promise);
    const discard = () => {
      this.operations.delete(promise);
      if (this.tasks.get(backend_key) === pending) this.tasks.delete(backend_key);
    };
    // One observer owns a background rejection even when a cached picker returns early.
    void promise.then(discard, (error) => {
      discard();
      if (!is_shutdown_business_cancellation(error)) console.warn(error);
    });
    return pending;
  }
  private async read(
    backend_key: ConversationBackendKey,
    refresh: boolean,
    signal: AbortSignal,
  ): Promise<BackendSnapshot> {
    try {
      const command = await this.mailbox.put_model_list_request(
        { backend_key, refresh },
        { signal },
      );
      aborted(signal);
      const result = await this.mailbox.wait_result(command.command_id, MODEL_LIST_LIMIT_SECONDS, {
        signal,
      });
      aborted(signal);
      if (result === null) {
        await this.mailbox.withdraw_waiting(
          command.command_id,
          CommandSettlementReason.model_list_limit_passed,
          { signal },
        );
      } else if (result.model_list !== null && result.model_list !== undefined) {
        const snapshot = this.decode(stringify_python_json(result.model_list));
        if (snapshot.backend_key !== backend_key)
          throw new ValueError('unexpected machine snapshot');
        return this.keep(backend_key, snapshot);
      }
    } catch (error) {
      if (!is_os_error(error) && !(error instanceof ValueError)) throw error;
      console.warn(`The Mac's ${backend_key} model list was not read: ${python_repr(error)}`);
    }
    aborted(signal);
    return this.answer(backend_key, this.retained(backend_key));
  }
  private keep(backend_key: ConversationBackendKey, snapshot: BackendSnapshot): BackendSnapshot {
    this.admit();
    const previous = this.retained(backend_key);
    if (previous?.models_complete && !snapshot.models_complete)
      snapshot = {
        ...snapshot,
        available_models: previous.available_models,
        reasoning_effort_options: previous.reasoning_effort_options,
        default_model_id: previous.default_model_id,
        default_reasoning_effort: previous.default_reasoning_effort,
        models_complete: true,
      };
    const body = stringify_python_json(as_object(snapshot, 'BackendSnapshot'));
    this.database.write((tx) =>
      tx.run(
        'INSERT INTO machine_backend_snapshots VALUES (?,?,?) ' +
          'ON CONFLICT(machine_id,backend_key) DO UPDATE SET snapshot_json=excluded.snapshot_json ' +
          'WHERE snapshot_json != excluded.snapshot_json',
        [this.machine_id, backend_key, body],
      ),
    );
    return snapshot;
  }
  private answer(
    backend_key: ConversationBackendKey,
    kept: BackendSnapshot | null,
  ): BackendSnapshot {
    return (
      kept ?? {
        backend_key,
        installed: false,
        executable_path: null,
        version: null,
        identity: null,
        available_models: [],
        reasoning_effort_options: [],
        default_model_id: null,
        default_reasoning_effort: null,
        update_advisory: null,
        diagnoses: ['No model list has been read from the Mac yet.'],
        models_complete: false,
      }
    );
  }
  connected(): void {
    this.admit();
    for (const backend of Object.values(ConversationBackendKey)) {
      this.tasks.get(backend)?.controller.abort(cancelled());
      this.start_read(backend, true);
    }
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.closed) {
      this.closed = true;
      for (const pending of this.tasks.values()) pending.controller.abort(cancelled());
      const drain = Promise.allSettled([...this.operations]).then((results) => {
        const failures = results.filter(
          (result): result is PromiseRejectedResult =>
            result.status === 'rejected' && !is_shutdown_business_cancellation(result.reason),
        );
        if (failures.length)
          throw new AggregateError(
            failures.map((result) => result.reason),
            'Catalog shutdown failed.',
          );
      });
      void drain.then(this.resolve_completion, this.reject_completion);
    }
    return observe_shutdown(
      this.completion,
      options,
      'Executor catalog reads remain unfinished at the shutdown deadline.',
    );
  }
  settled(): Promise<void> {
    return this.completion;
  }
}
