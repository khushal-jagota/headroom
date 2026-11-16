/** Native work belongs to the runner. These queues own ordering and durable retries. */
import { constants } from 'node:os';
import * as protocol from '../../protocol.ts';
import type { CommandResult, MailboxCommand } from '../../wire.generated.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { ServerLifecycleError } from '../../../lifecycle/contracts.ts';
import { OUTBOX_RETRY_INITIAL_SECONDS, OUTBOX_RETRY_MAX_SECONDS } from '../configuration.ts';
import { mailbox_failure as failure_name } from './diagnostics.ts';
import type { CanonicalCatalogResult, QueueDependencies } from './contracts.ts';
import { MailboxLifetime, MailboxCleanupFailure } from './lifetime.ts';
import { MailboxRunCancelled } from './contracts.ts';
function storage_error(error: unknown): boolean {
  const code = (error as { code?: unknown } | null)?.code;
  return (
    typeof code === 'string' && (code.startsWith('SQLITE_') || Object.hasOwn(constants.errno, code))
  );
}
export class MacCommandQueues {
  private readonly dependencies: QueueDependencies;
  private readonly lifetime: MailboxLifetime;
  private readonly invocation = new AbortController();
  private readonly queues = new Map<string, MailboxCommand[]>();
  private replaying: Promise<void> | undefined;
  private replay_pending = false;
  constructor(dependencies: QueueDependencies) {
    this.dependencies = dependencies;
    this.lifetime = new MailboxLifetime(dependencies.on_failure);
  }
  submit(command: MailboxCommand): void {
    this.lifetime.assert_admission();
    const child = command.child_command;
    if (child == null) {
      void this.lifetime.own(() => this.model_list(command));
      return;
    }
    if (child.operation === 'stop') {
      void this.lifetime.own(() => this.child_command(command));
      return;
    }
    const queue = this.queues.get(child.child.child_id);
    if (queue) {
      queue.push(command);
      return;
    }
    const created = [command];
    this.queues.set(child.child.child_id, created);
    void this.lifetime.own(async () => {
      try {
        while (this.lifetime.alive && created.length) {
          await this.child_command(created[0]!);
          created.shift();
        }
      } finally {
        if (this.queues.get(child.child.child_id) === created)
          this.queues.delete(child.child.child_id);
      }
    });
  }
  republish(): Promise<void> {
    this.lifetime.assert_admission();
    this.replay_pending = true;
    if (!this.replaying) {
      const task = this.lifetime.own(async () => {
        while (this.lifetime.alive && this.replay_pending) {
          this.replay_pending = false;
          await this.replay();
        }
      });
      this.replaying = task;
      const finished = () => {
        if (this.replaying === task) this.replaying = undefined;
        if (this.lifetime.alive && this.replay_pending) void this.republish();
      };
      void task.then(finished, finished);
    }
    return this.replaying;
  }
  private async replay(): Promise<void> {
    let delay = OUTBOX_RETRY_INITIAL_SECONDS;
    while (this.lifetime.alive) {
      try {
        const cursor = await this.dependencies.journal.unpublished();
        let snapshot_error: unknown,
          snapshot_failed = false;
        try {
          for await (const row of cursor) {
            if (!this.lifetime.alive) return;
            let publish_error: unknown,
              publish_failed = false;
            try {
              await this.dependencies.runner.publish_owed_outputs(row.command, row.result);
            } catch (error) {
              publish_error = error;
              publish_failed = true;
              throw error;
            } finally {
              const closed = await Promise.allSettled([row.command.close(), row.result.close()]);
              const errors = closed.flatMap((value) =>
                value.status === 'rejected' ? [value.reason] : [],
              );
              if (errors.length)
                throw new MailboxCleanupFailure(
                  publish_failed ? [publish_error, ...errors] : errors,
                  'Owed-output reader cleanup failed.',
                );
            }
          }
        } catch (error) {
          snapshot_error = error;
          snapshot_failed = true;
          throw error;
        } finally {
          try {
            await cursor.close();
          } catch (error) {
            throw new MailboxCleanupFailure(
              snapshot_failed ? [snapshot_error, error] : [error],
              'Owed-output cursor cleanup failed.',
            );
          }
        }
        this.dependencies.result_finished();
        return;
      } catch (error) {
        if (error instanceof MailboxCleanupFailure || !this.lifetime.alive) throw error;
        this.dependencies.journal.assert_healthy();
        console.warn('Owed command output was not published:', failure_name(error));
        await this.lifetime.pause(delay);
        delay = Math.min(delay * 2, OUTBOX_RETRY_MAX_SECONDS);
      }
    }
  }
  private async child_command(command: MailboxCommand): Promise<void> {
    let delay = OUTBOX_RETRY_INITIAL_SECONDS;
    while (this.lifetime.alive) {
      try {
        await this.dependencies.runner.run(command.child_command!, this.invocation.signal);
        this.dependencies.result_finished();
        return;
      } catch (error) {
        if (this.invocation.signal.aborted) {
          console.debug(
            `Mac command canceled during stop command=${command.command_id}:`,
            failure_name(error),
          );
          return;
        }
        if (!this.lifetime.alive) throw error;
        this.dependencies.journal.assert_healthy();
        console.warn(
          `Command result was not recorded command=${command.command_id}:`,
          failure_name(error),
        );
        await this.lifetime.pause(delay);
        delay = Math.min(delay * 2, OUTBOX_RETRY_MAX_SECONDS);
      }
    }
  }
  private async model_list(command: MailboxCommand): Promise<void> {
    let snapshot: CanonicalCatalogResult | undefined;
    let finished = false;
    let primary: unknown,
      failed = false;
    const { runner, catalog, journal } = this.dependencies;
    try {
      if (!runner.admission_stopped) {
        try {
          snapshot = await catalog.read_result(command.command_id, command.model_list_request!);
        } catch (error) {
          if (error instanceof ServerLifecycleError) throw error;
          console.warn('Model list read failed:', failure_name(error));
        }
      }
      const empty: CommandResult = {
        command_id: command.command_id,
        child_outcome: null,
        model_list: null,
      };
      const fallback =
        snapshot === undefined
          ? Buffer.from(JSON.stringify(protocol.encode_value(empty, 'CommandResult')))
          : undefined;
      let delay = OUTBOX_RETRY_INITIAL_SECONDS;
      // The complete task was admitted before the catalog call. Its first result
      // write remains allowed if shutdown starts during that already-owned call.
      while (true) {
        try {
          if (snapshot)
            await journal.finish_stream(command.command_id, await snapshot.open(), false);
          else await journal.finish(command.command_id, fallback!, false);
          finished = true;
          break;
        } catch (error) {
          if (!this.lifetime.alive) throw error;
          journal.assert_healthy();
          if (!storage_error(error)) throw error;
          console.warn('Model list result was not recorded:', failure_name(error));
          await this.lifetime.pause(delay);
          if (!this.lifetime.alive) return;
          delay = Math.min(delay * 2, OUTBOX_RETRY_MAX_SECONDS);
        }
      }
    } catch (error) {
      primary = error;
      failed = true;
      throw error;
    } finally {
      try {
        await snapshot?.close();
      } catch (error) {
        throw new MailboxCleanupFailure(
          failed ? [primary, error] : [error],
          'Catalog result cleanup failed.',
        );
      }
    }
    if (finished) this.dependencies.result_finished();
  }
  stop(options: StopOptions): Promise<void> {
    this.queues.clear();
    this.replay_pending = false;
    this.lifetime.seal();
    this.invocation.abort(new MailboxRunCancelled(options));
    return this.lifetime.stop(options);
  }
  settled(): Promise<void> {
    return this.lifetime.settled();
  }
}
