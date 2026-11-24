/** Durable browser progress; the existing move service remains the relocation authority. */
import { randomUUID } from 'node:crypto';
import { observe_shutdown } from '../conversation/shutdown.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { Database, ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { read_machine } from '../machines/data.ts';
import { PlannerError } from '../core/contracts.ts';
import type { WorkMoveOperation, WorkMoveOwner, WorkMovePhase } from './contracts.ts';
import { read_work, unfinished_work, work_move_status } from './data.ts';
import { move_work } from './service.ts';
import type { WorkMoveServices } from './service.ts';

export function read_operations(sql: ReadSql): WorkMoveOperation[] {
  return sql
    .all<{ operation_json: string }>(
      'SELECT operation_json FROM work_move_operations ORDER BY started_at_ms DESC,operation_id',
    )
    .map((row) => JSON.parse(row.operation_json) as WorkMoveOperation);
}
/** Delivery events only inspect active moves, not retained operation history. */
export function read_active_operations(sql: ReadSql): WorkMoveOperation[] {
  return sql
    .all<{ operation_json: string }>(
      'SELECT operation_json FROM work_move_operations WHERE finished_at_ms IS NULL',
    )
    .map((row) => JSON.parse(row.operation_json) as WorkMoveOperation);
}
export function write_operation(tx: TransactionSql, operation: WorkMoveOperation): void {
  tx.run(
    'INSERT INTO work_move_operations(operation_id,started_at_ms,finished_at_ms,operation_json) VALUES(?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET finished_at_ms=excluded.finished_at_ms,operation_json=excluded.operation_json',
    [
      operation.operation_id,
      operation.started_at_ms,
      operation.finished_at_ms,
      JSON.stringify(operation),
    ],
  );
}
function owner_key(owner: WorkMoveOwner): string {
  return owner.kind === 'chief' ? 'chief' : `${owner.kind}:${owner.id}`;
}
export function set_phase(
  operation: WorkMoveOperation,
  index: number,
  phase: WorkMovePhase,
  now: number,
): void {
  const item = operation.items[index]!;
  if (item.phase === phase) return;
  const previous = item.phases.at(-1);
  if (previous) previous.finished_at_ms = now;
  item.phase = phase;
  item.phases.push({
    phase,
    started_at_ms: now,
    finished_at_ms: phase === 'failed' || phase === 'complete' ? now : null,
  });
}
/** A process restart cannot safely replay a partially completed relocation. */
export function interrupt_operations(services: Pick<WorkMoveServices, 'database'>): void {
  services.database.write((tx) => {
    for (const operation of read_active_operations(tx)) {
      if (operation.finished_at_ms !== null) continue;
      const now = Date.now();
      for (const [index, item] of operation.items.entries()) {
        if (item.result !== null) continue;
        let moved = false;
        try {
          moved = read_work(tx, item.owner).machine_id === operation.machine_id;
        } catch {
          /* Removed work still receives an explicit failure. */
        }
        item.result = {
          owner: item.owner,
          title: item.title,
          machine_id: operation.machine_id,
          moved,
          error:
            'The server stopped before the move finished. Check the current machine and conversation, then retry the move or start a new conversation there.',
        };
        set_phase(operation, index, 'failed', now);
      }
      operation.finished_at_ms = now;
      write_operation(tx, operation);
    }
  });
}
export function create_operation(
  services: WorkMoveServices,
  machine_id: string,
  owner?: WorkMoveOwner,
): WorkMoveOperation {
  return services.database.write((tx) => {
    read_machine(tx, machine_id);
    if (owner === undefined && work_move_status(tx).move_everything_machine_id !== machine_id)
      throw new PlannerError(
        'validation',
        'Move everything is available on the new server machine after a successful server move.',
      );
    const owners = owner === undefined ? unfinished_work(tx) : [owner];
    const active = new Set(
      read_active_operations(tx)
        .filter((operation) => operation.finished_at_ms === null)
        .flatMap((operation) =>
          operation.items
            .filter((item) => item.result === null)
            .map((item) => owner_key(item.owner)),
        ),
    );
    if (owners.some((selected) => active.has(owner_key(selected))))
      throw new PlannerError(
        'validation',
        'This work already has a move in progress. Follow its progress before another move.',
      );
    const now = Date.now();
    const operation: WorkMoveOperation = {
      operation_id: randomUUID(),
      machine_id,
      everything: owner === undefined,
      started_at_ms: now,
      finished_at_ms: null,
      items: owners.map((selected) => ({
        conversation_id: null,
        owner: selected,
        title: read_work(tx, selected).title,
        phase: 'queued',
        phases: [{ phase: 'queued', started_at_ms: now, finished_at_ms: null }],
        result: null,
      })),
    };
    write_operation(tx, operation);
    return operation;
  });
}
/** Setup reports carry the destination's actual recovery/start boundary. */
export function report_move_setup(
  tx: TransactionSql,
  conversation_id: string,
  step: string,
  now_ms: number,
): void {
  if (step !== 'preparing_workspace' && step !== 'starting_worker') return;
  for (const operation of read_active_operations(tx)) {
    if (operation.finished_at_ms !== null) continue;
    const index = operation.items.findIndex(
      (item) => item.conversation_id === conversation_id && item.result === null,
    );
    if (index < 0) continue;
    const item = operation.items[index]!;
    // A late preparation report must not move the screen backward.
    if (item.phase === 'starting' && step === 'preparing_workspace') return;
    set_phase(operation, index, step === 'starting_worker' ? 'starting' : 'recovering', now_ms);
    write_operation(tx, operation);
  }
}
export async function run_operation(
  services: WorkMoveServices,
  accepted: WorkMoveOperation,
  assert_open: () => void = () => {},
): Promise<void> {
  const update = (mutate: (operation: WorkMoveOperation) => void) =>
    services.database.write((tx) => {
      const row = tx.get<{ operation_json: string }>(
        'SELECT operation_json FROM work_move_operations WHERE operation_id=?',
        [accepted.operation_id],
      )!;
      const operation = JSON.parse(row.operation_json) as WorkMoveOperation;
      mutate(operation);
      write_operation(tx, operation);
    });
  for (const [index, item] of accepted.items.entries()) {
    let pending = false;
    const result = await move_work(
      services,
      { owner: item.owner, machine_id: accepted.machine_id },
      (phase, conversation_id, delivery_pending) => {
        pending = delivery_pending ?? pending;
        assert_open();
        update((operation) => {
          if (operation.items[index]!.result !== null) return;
          if (conversation_id !== undefined)
            operation.items[index]!.conversation_id = conversation_id;
          set_phase(operation, index, phase, Date.now());
        });
      },
    );
    update((operation) => {
      if (pending || operation.items[index]!.result !== null) return;
      operation.items[index]!.result = result;
      set_phase(operation, index, result.error === null ? 'complete' : 'failed', Date.now());
    });
  }
  update((operation) => {
    if (operation.items.every((item) => item.result !== null))
      operation.finished_at_ms = Date.now();
  });
}

/** Both HTTP hosts and the production producer lifecycle share this task owner. */
export class WorkMoveOperationRuntime {
  private readonly tasks = new Set<Promise<void>>();
  private closing = false;
  constructor(database: Database) {
    interrupt_operations({ database });
  }
  assert_open(): void {
    if (this.closing)
      throw new PlannerError(
        'validation',
        'The server stopped before the move finished. Check its destination and retry after restart.',
      );
  }
  submit(services: WorkMoveServices, operation: WorkMoveOperation): void {
    this.assert_open();
    const task = new Promise<void>((resolve) => setImmediate(resolve)).then(() =>
      run_operation(services, operation, () => this.assert_open()),
    );
    this.tasks.add(task);
    void task.then(
      () => this.tasks.delete(task),
      (error) => {
        this.tasks.delete(task);
        console.error('Work move operation failed', error);
      },
    );
  }
  seal(): void {
    this.closing = true;
  }
  settled(): Promise<void> {
    return Promise.allSettled([...this.tasks]).then(() => {});
  }
  close(stop: StopOptions): Promise<void> {
    this.seal();
    return observe_shutdown(
      this.settled(),
      stop,
      'Work move operations did not stop before the shutdown deadline.',
      ['work moves'],
    );
  }
}
const runtimes = new WeakMap<Database, WorkMoveOperationRuntime>();
export function work_move_runtime(database: Database): WorkMoveOperationRuntime {
  let runtime = runtimes.get(database);
  if (!runtime) {
    runtime = new WorkMoveOperationRuntime(database);
    runtimes.set(database, runtime);
  }
  return runtime;
}
