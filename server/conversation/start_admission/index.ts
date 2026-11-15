/** Admission owns pending launch reservations, independently of running turns. */
import { AsyncLocalStorage } from 'node:async_hooks';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import type { ChildReference, TurnToken } from '../backends/contracts.ts';
import { AUTOMATIC_START_INTERVAL_SECONDS, MAX_AUTOMATIC_STARTS } from './config.ts';
import type {
  AdmissionDatabase,
  ReservationPhase,
  StartRequest,
  StartSnapshot,
} from './contracts.ts';

const manual_starts = new AsyncLocalStorage<boolean>();
export function manual_start_exempt(): boolean {
  return manual_starts.getStore() === true;
}
export function explicit_start<T>(operation: () => T): T {
  return manual_starts.run(true, operation);
}
interface Reservation {
  request: StartRequest;
  active: boolean;
  uncertain: boolean;
  turn: TurnToken | null;
  source: object | null;
  phase: ReservationPhase;
  permit: string | null;
  managed: boolean;
  eligible_order: number | null;
}
function reservation(request: StartRequest): Reservation {
  return {
    request,
    active: false,
    uncertain: false,
    turn: null,
    source: null,
    phase: 'eligible',
    permit: null,
    managed: false,
    eligible_order: null,
  };
}
function turn_key(turn: TurnToken): string {
  return JSON.stringify([turn.conversation_id, turn.turn_number]);
}

export class AutomaticStartAdmission {
  readonly limit: number;
  readonly #clock: () => number;
  readonly #requests = new Map<string, Reservation>();
  readonly #destinations = new Map<string, Map<string, Reservation>>();
  readonly #turn_destinations = new Map<string, string>();
  readonly #next_start = new Map<string, number>();
  readonly #last_permit = new Map<string, string>();
  readonly #observed_turns = new Map<string, number>();
  #eligibility_sequence = 0;
  #reachable: (destination: string) => boolean = (destination) => destination === HOME_MACHINE_ID;
  constructor(options: { limit?: number; clock?: () => number } = {}) {
    this.limit = options.limit ?? MAX_AUTOMATIC_STARTS;
    if (this.limit < 2)
      throw new Error('Automatic starts need a Worker slot and a supervisor slot');
    this.#clock = options.clock ?? (() => performance.now() / 1000);
  }
  configure_destinations(reachable: (destination: string) => boolean): void {
    this.#reachable = reachable;
  }
  #members(destination: string): Reservation[] {
    return [...(this.#destinations.get(destination)?.values() ?? [])];
  }
  #put(entry: Reservation): Reservation {
    this.#requests.set(entry.request.identity, entry);
    let members = this.#destinations.get(entry.request.destination);
    if (!members) this.#destinations.set(entry.request.destination, (members = new Map()));
    members.set(entry.request.identity, entry);
    if (entry.turn) this.#turn_destinations.set(turn_key(entry.turn), entry.request.destination);
    return entry;
  }
  #remove(identity: string): Reservation | undefined {
    const entry = this.#requests.get(identity);
    if (!entry) return;
    this.#requests.delete(identity);
    const members = this.#destinations.get(entry.request.destination)!;
    members.delete(identity);
    this.#forget_turn(entry.turn, entry.request.destination);
    if (!members.size) this.#destinations.delete(entry.request.destination);
    return entry;
  }
  #forget_turn(turn: TurnToken | null, destination: string): void {
    if (
      turn &&
      !this.#members(destination).some(
        (entry) => entry.turn && turn_key(entry.turn) === turn_key(turn),
      )
    )
      this.#turn_destinations.delete(turn_key(turn));
  }
  #for_turn(turn: TurnToken): Reservation[] {
    const destination = this.#turn_destinations.get(turn_key(turn));
    return destination === undefined
      ? []
      : this.#members(destination).filter(
          (entry) => entry.turn && turn_key(entry.turn) === turn_key(turn),
        );
  }
  #mark_eligible(entry: Reservation): void {
    if (entry.eligible_order === null && this.#reachable(entry.request.destination))
      entry.eligible_order = ++this.#eligibility_sequence;
  }
  #refresh(destination?: string): void {
    const members =
      destination === undefined ? this.#requests.values() : this.#members(destination);
    for (const entry of members) {
      if (!this.#reachable(entry.request.destination)) {
        entry.active = false;
        entry.eligible_order = null;
      } else if (entry.phase === 'eligible') this.#mark_eligible(entry);
    }
  }
  #entries(identities: Iterable<string | null>): Reservation[] {
    return [...identities].flatMap((identity) => {
      const entry = identity === null ? undefined : this.#requests.get(identity);
      return entry ? [entry] : [];
    });
  }
  mark_eligible(identities: Iterable<string | null>): void {
    for (const entry of this.#entries(identities)) {
      if (entry.phase === 'queued' || entry.phase === 'eligible') {
        entry.phase = 'eligible';
        this.#mark_eligible(entry);
      }
    }
  }
  try_start(request: StartRequest): boolean {
    const entry = this.#requests.get(request.identity) ?? this.#put(reservation(request));
    return (
      entry.phase !== 'queued' && entry.phase !== 'failed' && this.#admit_group([request.identity])
    );
  }
  #admit_group(identities: Iterable<string>): boolean {
    const entries = this.#entries(identities);
    if (!entries.length) return true;
    const destination = entries[0].request.destination;
    if (entries.some((entry) => entry.request.destination !== destination))
      throw new Error('One start group cannot span destinations');
    this.#refresh(destination);
    const members = this.#members(destination);
    if (!entries.every((entry) => this.#reachable(entry.request.destination))) return false;
    for (const entry of entries) if (!entry.active) this.#mark_eligible(entry);
    let permit = entries.find((entry) => entry.active)?.permit;
    if (permit === undefined) {
      const active = new Map(
        members.filter((entry) => entry.active).map((entry) => [entry.permit, entry]),
      );
      if (
        active.size >= this.limit ||
        this.#clock() < (this.#next_start.get(destination) ?? -Infinity)
      )
        return false;
      const supervisor = entries.some((entry) => entry.request.work_class === 'supervisor');
      if (!supervisor) {
        if (
          destination === HOME_MACHINE_ID &&
          [...active.values()].filter((entry) => entry.request.work_class === 'worker').length >=
            this.limit - 1
        )
          return false;
        if (
          members.some(
            (entry) =>
              entry.request.work_class === 'supervisor' &&
              !entry.active &&
              entry.eligible_order !== null &&
              this.#reachable(entry.request.destination),
          )
        )
          return false;
      }
      const work_class = supervisor ? 'supervisor' : 'worker';
      const order = Math.min(
        ...entries.flatMap((entry) =>
          entry.eligible_order === null ? [] : [entry.eligible_order],
        ),
      );
      if (
        members.some(
          (entry) =>
            entry.request.work_class === work_class &&
            !entry.active &&
            entry.eligible_order !== null &&
            entry.eligible_order < order &&
            this.#reachable(entry.request.destination),
        )
      )
        return false;
      permit = entries[0].request.identity;
      this.#last_permit.set(destination, permit);
      this.#next_start.set(destination, this.#clock() + AUTOMATIC_START_INTERVAL_SECONDS);
    }
    for (const entry of entries) {
      entry.active = true;
      entry.permit = permit ?? null;
      if (entry.phase !== 'attempted') entry.phase = 'preparing';
    }
    return true;
  }
  handoff(identity: string | null): void {
    for (const entry of this.#entries([identity])) entry.managed = true;
  }
  #refund(permit: string | null, refundable: boolean, destination: string): void {
    if (
      refundable &&
      permit !== null &&
      this.#last_permit.get(destination) === permit &&
      !this.#members(destination).some((entry) => entry.active && entry.permit === permit)
    ) {
      this.#next_start.set(
        destination,
        Math.min(this.#next_start.get(destination) ?? this.#clock(), this.#clock()),
      );
      this.#last_permit.delete(destination);
    }
  }
  queue(identity: string | null, options: { eligible?: boolean } = {}): void {
    const entry = this.#entries([identity])[0];
    if (!entry) return;
    const permit = entry.permit;
    const refundable = entry.phase === 'preparing';
    if (entry.phase !== 'failed') entry.phase = options.eligible ? 'eligible' : 'queued';
    if (!options.eligible) entry.eligible_order = null;
    else this.#mark_eligible(entry);
    entry.active = false;
    entry.permit = null;
    const previous = entry.turn;
    entry.turn = null;
    this.#forget_turn(previous, entry.request.destination);
    entry.source = null;
    entry.uncertain = false;
    entry.managed = true;
    this.#refund(permit, refundable, entry.request.destination);
  }
  reuse_outcome(identity: string | null, options: { queued: boolean }): void {
    const entry = this.#entries([identity])[0];
    if (!entry) return;
    if (options.queued) {
      if (entry.phase !== 'attempted' && !entry.uncertain) this.queue(identity);
    } else if (entry.turn === null && !entry.managed) {
      this.#remove(entry.request.identity);
      this.#refund(entry.permit, entry.phase === 'preparing', entry.request.destination);
    }
  }
  wait_if_unadmitted(identity: string | null): void {
    const entry = this.#entries([identity])[0];
    if (entry && !entry.active && (entry.phase === 'eligible' || entry.phase === 'queued'))
      this.queue(identity);
  }
  begin_queued(identities: Iterable<string | null>): boolean {
    const entries = this.#entries(identities);
    for (const entry of entries) if (entry.phase === 'failed') entry.phase = 'queued';
    const keys = entries.map((entry) => entry.request.identity);
    this.mark_eligible(keys);
    return this.#admit_group(keys);
  }
  priority(identities: Iterable<string | null>): readonly [number, number] {
    const entries = this.#entries(identities);
    const orders = entries.flatMap((entry) =>
      entry.eligible_order === null ? [] : [entry.eligible_order],
    );
    return [
      !entries.length || entries.some((entry) => entry.request.work_class === 'supervisor') ? 0 : 1,
      orders.length ? Math.min(...orders) : this.#eligibility_sequence + 1,
    ];
  }
  transfer(preparation: string, request: StartRequest): boolean {
    const entry = this.#remove(preparation);
    if (!entry) throw new Error('Preparation does not own a reservation');
    if (this.#requests.has(request.identity)) {
      this.#put(entry);
      throw new Error('Actual claim identity already owns a reservation');
    }
    entry.request = request;
    this.#put(entry);
    this.#refresh();
    return entry.active;
  }
  has_requests(identities: Iterable<string | null>): boolean {
    return this.#entries(identities).length > 0;
  }
  attempted(identities: Iterable<string | null>): boolean {
    return this.#entries(identities).some((entry) => entry.phase === 'attempted');
  }
  finish(identity: string): void {
    this.#remove(identity);
  }
  finish_unbound(identity: string, options: { inactive_only?: boolean } = {}): void {
    const entry = this.#requests.get(identity);
    if (entry && entry.turn === null && !entry.managed && (!options.inactive_only || !entry.active))
      this.#remove(identity);
  }
  uncertain(identity: string): void {
    const entry = this.#requests.get(identity);
    if (entry) entry.uncertain = true;
  }
  bind(identities: Iterable<string | null>, turn: TurnToken): void {
    for (const entry of this.#entries(identities)) {
      const previous = entry.turn;
      entry.turn = turn;
      this.#forget_turn(previous, entry.request.destination);
      this.#turn_destinations.set(turn_key(turn), entry.request.destination);
    }
  }
  observe(turn: TurnToken): void {
    for (const entry of this.#for_turn(turn)) this.#remove(entry.request.identity);
  }
  allow_dispatch(turn: TurnToken): boolean {
    return this.#admit_group(this.#for_turn(turn).map((entry) => entry.request.identity));
  }
  snapshots(): readonly StartSnapshot[] {
    this.#refresh();
    return [...this.#requests.values()].map((entry) => ({
      request: entry.request,
      state:
        entry.phase === 'queued' || (entry.phase === 'failed' && entry.managed)
          ? 'queued'
          : !entry.active
            ? 'deferred'
            : entry.uncertain
              ? 'uncertain'
              : 'active',
      turn: entry.turn,
      permit_identity: entry.active ? entry.permit : null,
    }));
  }
  poll_delay(fallback: number): number {
    this.#refresh();
    return this.#requests.size ? Math.min(fallback, AUTOMATIC_START_INTERVAL_SECONDS) : fallback;
  }
  retain_deferred(prefix: string, identities: ReadonlySet<string>): void {
    for (const [identity, entry] of this.#requests) {
      if (
        identity.startsWith(prefix) &&
        !identities.has(identity) &&
        !entry.active &&
        entry.turn === null &&
        !entry.uncertain &&
        !entry.managed
      )
        this.#remove(identity);
    }
  }
  requests_for(identities: Iterable<string | null>): readonly StartRequest[] {
    return this.#entries(identities).map((entry) => entry.request);
  }
  persist_bindings(database: AdmissionDatabase, turn: TurnToken): void {
    const requests = this.#for_turn(turn).map((entry) => entry.request);
    if (!requests.length) return;
    database.write((tx) =>
      write_bindings(
        tx,
        turn,
        requests.filter(
          (request) =>
            !tx.get(
              'SELECT 1 FROM automatic_start_bindings WHERE identity=? AND work_class=? AND destination=? AND conversation_id=? AND turn_number=?',
              [
                request.identity,
                request.work_class,
                request.destination,
                turn.conversation_id,
                turn.turn_number,
              ],
            ),
        ),
      ),
    );
  }
  restore_bindings(sql: ReadSql, turn: TurnToken): void {
    for (const request of sql.all<StartRequest>(
      'SELECT identity,work_class,destination FROM automatic_start_bindings WHERE conversation_id=? AND turn_number=?',
      [turn.conversation_id, turn.turn_number],
    ))
      if (!this.#requests.has(request.identity))
        this.#put({ ...reservation(request), turn, phase: 'attempted', managed: true });
  }
  accept(database: AdmissionDatabase, turn: TurnToken): void {
    if (this.#observed_turns.get(turn.conversation_id) === turn.turn_number) return;
    const bound = database.read((sql) =>
      sql.get('SELECT 1 FROM automatic_start_bindings WHERE conversation_id=? AND turn_number=?', [
        turn.conversation_id,
        turn.turn_number,
      ]),
    );
    if (bound) this.reject(database, turn);
    else this.observe(turn);
    this.#observed_turns.set(
      turn.conversation_id,
      Math.max(this.#observed_turns.get(turn.conversation_id) ?? 0, turn.turn_number),
    );
  }
  bind_child(database: AdmissionDatabase, turn: TurnToken, child: ChildReference): void {
    database.write((tx) =>
      tx.run(
        'UPDATE automatic_start_bindings SET executor_incarnation=?,child_id=? WHERE conversation_id=? AND turn_number=?',
        [child.executor_incarnation, child.child_id, turn.conversation_id, turn.turn_number],
      ),
    );
  }
  reject(database: AdmissionDatabase, turn: TurnToken): void {
    database.write((tx) =>
      tx.run('DELETE FROM automatic_start_bindings WHERE conversation_id=? AND turn_number=?', [
        turn.conversation_id,
        turn.turn_number,
      ]),
    );
    this.observe(turn);
  }
  bind_source(turn: TurnToken, source: object | null): void {
    for (const entry of this.#for_turn(turn)) {
      entry.source = source;
      entry.phase = source === null ? 'preparing' : 'attempted';
    }
  }
  retire_source(database: AdmissionDatabase, source: object): void {
    const turns = new Map(
      [...this.#requests.values()].flatMap((entry) =>
        entry.source === source && entry.turn ? [[turn_key(entry.turn), entry.turn] as const] : [],
      ),
    );
    for (const turn of turns.values()) this.reject(database, turn);
  }
  retire_remote(database: AdmissionDatabase, reference: ChildReference): void {
    const turns = database.read((sql) =>
      sql.all<{ turn_number: number }>(
        'SELECT DISTINCT turn_number FROM automatic_start_bindings WHERE conversation_id=? AND executor_incarnation=? AND child_id=?',
        [reference.conversation_id, reference.executor_incarnation, reference.child_id],
      ),
    );
    for (const { turn_number } of turns)
      this.reject(database, { conversation_id: reference.conversation_id, turn_number });
  }
  settle_ids(database: AdmissionDatabase, identities: Iterable<string | null>): void {
    const keys = [...identities].filter((identity): identity is string => identity !== null);
    if (!keys.length) return;
    database.write((tx) => {
      for (const identity of keys)
        tx.run('DELETE FROM automatic_start_bindings WHERE identity=?', [identity]);
    });
    for (const identity of keys) this.#remove(identity);
  }
  failed_attempt(database: AdmissionDatabase, turn: TurnToken): void {
    const entries = this.#for_turn(turn);
    if (entries.some((entry) => entry.phase === 'attempted')) {
      for (const entry of entries) entry.uncertain = true;
      this.#refresh();
      return;
    }
    this.settle_ids(
      database,
      entries.map((entry) => entry.request.identity),
    );
    for (const entry of entries)
      this.#put({
        ...entry,
        active: false,
        permit: null,
        turn: null,
        source: null,
        phase: 'failed',
        eligible_order: null,
      });
  }
  failed_sender(database: AdmissionDatabase, identity: string | null): void {
    const entry = this.#entries([identity])[0];
    if (!entry || entry.phase === 'queued') return;
    if (entry.phase === 'attempted') {
      entry.uncertain = true;
      this.#refresh();
    } else this.settle_ids(database, [identity]);
  }
}

/** Called inside the atomic turn reservation, without a second writer. */
export function write_bindings(
  tx: TransactionSql,
  turn: TurnToken,
  requests: readonly StartRequest[],
): void {
  for (const request of requests)
    tx.run(
      'INSERT INTO automatic_start_bindings (identity,work_class,destination,conversation_id,turn_number) VALUES (?,?,?,?,?) ' +
        'ON CONFLICT(identity) DO UPDATE SET conversation_id=excluded.conversation_id,turn_number=excluded.turn_number,executor_incarnation=NULL,child_id=NULL',
      [
        request.identity,
        request.work_class,
        request.destination,
        turn.conversation_id,
        turn.turn_number,
      ],
    );
}
