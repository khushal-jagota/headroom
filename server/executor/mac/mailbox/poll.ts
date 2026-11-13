/** Record before handoff. An interrupted committed handoff faults the whole host. */
import * as protocol from '../../protocol.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { is_shutdown_business_cancellation } from '../../../conversation/shutdown.ts';
import {
  COMMAND_POLL_HOLD_SECONDS,
  FILE_CHUNK_BYTES,
  LEASE_INTERVAL_SECONDS,
  OUTBOX_RETRY_INITIAL_SECONDS,
} from '../configuration.ts';
import { JOURNAL_INPUT_BYTES } from '../storage/journal_limits.ts';
import type { CanonicalReader } from '../storage/journal_contracts.ts';
import type { PollDependencies } from './contracts.ts';
import type { MacCommandQueues } from './queues.ts';
import { MailboxLifetime } from './lifetime.ts';
import { mailbox_failure } from './diagnostics.ts';
export async function read_canonical_body(reader: CanonicalReader): Promise<Uint8Array> {
  try {
    if (
      !Number.isSafeInteger(reader.byte_count) ||
      reader.byte_count < 0 ||
      reader.byte_count > JOURNAL_INPUT_BYTES
    )
      throw new RangeError('Journal command exceeds the native byte limit.');
    const bytes = new Uint8Array(reader.byte_count);
    let offset = 0;
    for await (const chunk of reader) {
      if (
        !chunk.byteLength ||
        chunk.byteLength > FILE_CHUNK_BYTES ||
        offset + chunk.byteLength > bytes.length
      )
        throw new Error('Journal command exceeded its byte count.');
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    if (offset !== bytes.length) throw new Error('Journal command ended before its byte count.');
    return bytes;
  } finally {
    await reader.close();
  }
}
export class MacPollLoop {
  private readonly dependencies: PollDependencies;
  private readonly queues: MacCommandQueues;
  private readonly lifetime: MailboxLifetime;
  private started = false;
  constructor(dependencies: PollDependencies, queues: MacCommandQueues) {
    this.dependencies = dependencies;
    this.queues = queues;
    this.lifetime = new MailboxLifetime(dependencies.on_failure);
  }
  start(): void {
    this.lifetime.assert_admission();
    if (this.started) return;
    this.started = true;
    void this.lifetime.own(() => this.run());
  }
  private async run(): Promise<void> {
    const { runner, journal, client } = this.dependencies;
    let delay = OUTBOX_RETRY_INITIAL_SECONDS;
    while (this.lifetime.alive) {
      try {
        await runner.planned_replay_finished(this.lifetime.signal);
      } catch (error) {
        if (!this.lifetime.alive && is_shutdown_business_cancellation(error)) return;
        throw error;
      }
      if (!this.lifetime.alive) return;
      try {
        const after = await journal.poll_cursor();
        if (!this.lifetime.alive) return;
        let bytes: Uint8Array;
        try {
          bytes = await client.poll_commands(
            after,
            COMMAND_POLL_HOLD_SECONDS,
            this.lifetime.signal,
          );
        } catch (error) {
          if (!this.lifetime.alive && is_shutdown_business_cancellation(error)) return;
          throw error;
        }
        if (!this.lifetime.alive) return;
        const fresh = await journal.record_poll_reply(bytes, this.dependencies.incarnation, after);
        let count = 0;
        try {
          for await (const row of fresh) {
            const command = protocol.decode_json(
              await read_canonical_body(row.command),
              'MailboxCommand',
            );
            this.queues.submit(command);
            count++;
          }
        } catch (error) {
          throw journal.delivery_failure('Committed mailbox command handoff failed.', error);
        } finally {
          await fresh.close();
        }
        delay = OUTBOX_RETRY_INITIAL_SECONDS;
        if (count < fresh.command_count) {
          void this.queues.republish();
          this.dependencies.result_finished();
        }
      } catch (error) {
        if (!this.lifetime.alive) throw error;
        journal.assert_healthy();
        console.warn('Mac command poll failed:', mailbox_failure(error));
        await this.lifetime.pause(delay);
        delay = Math.min(delay * 2, LEASE_INTERVAL_SECONDS);
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    return this.lifetime.stop(options);
  }
  settled(): Promise<void> {
    return this.lifetime.settled();
  }
}
