/**
 * The new server's side of a move. It starts held by its marker, answers the target's test
 * job over the local executor link, and on commit takes the next move number in one write.
 */
import { randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { closeSync, fsyncSync, openSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { UpdateBarrier } from '../environments/update_barrier.ts';
import { read_server_location } from '../server_link/data.ts';
import type {
  ServerMoveCommitReply,
  ServerMoveHeldMarker,
  ServerMoveProbeAnswer,
  ServerMoveProbeJob,
} from './contracts.ts';
import { SERVER_MOVE_HELD_FILE } from './contracts.ts';
import { SERVER_MOVE_KEPT_PROBE_JOBS } from './configuration.ts';
import { read_held_marker } from './hold.ts';
import { held_commit_state } from './logic/run.ts';

export interface HeldServerMoveOptions {
  readonly database: Database;
  readonly data_directory: string;
  readonly update_barrier: UpdateBarrier;
  readonly conversation: () => ConversationSystem | null;
}

function conflict(text: string): PlannerError {
  return new PlannerError('revision_conflict', text);
}

export class HeldServerMove {
  private readonly options: HeldServerMoveOptions;
  private marker: ServerMoveHeldMarker | null;
  private readonly jobs = new Map<string, string>();

  constructor(options: HeldServerMoveOptions) {
    this.options = options;
    this.marker = read_held_marker(options.data_directory);
  }

  /**
   * A commit that wrote the next move number but stopped before it removed the marker is
   * finished at startup, before the server decides whether it starts held.
   */
  recover(): void {
    const marker = this.marker;
    if (marker === null) return;
    const location = this.options.database.read(read_server_location);
    let state: 'commit' | 'done';
    try {
      state = held_commit_state(marker, location);
    } catch {
      return;
    }
    if (state === 'done') this.release(marker, false);
  }

  /** The routes ask this with the authenticated machine of a local executor link request. */
  require_target(machine_id: string): ServerMoveHeldMarker {
    const marker = this.marker;
    if (marker === null) throw new PlannerError('not_found', 'No move holds this server.');
    if (machine_id !== marker.target_machine_id)
      throw new PlannerError('agent_forbidden', 'Only the target machine of the move may ask.');
    return marker;
  }

  probe(machine_id: string): ServerMoveProbeJob {
    this.require_target(machine_id);
    const job: ServerMoveProbeJob = {
      job_id: randomUUID(),
      nonce: randomBytes(32).toString('hex'),
    };
    this.jobs.set(job.job_id, job.nonce);
    for (const job_id of this.jobs.keys()) {
      if (this.jobs.size <= SERVER_MOVE_KEPT_PROBE_JOBS) break;
      this.jobs.delete(job_id);
    }
    return job;
  }

  answer(machine_id: string, job_id: string, body: unknown): ServerMoveProbeAnswer {
    this.require_target(machine_id);
    const nonce = (body as { nonce?: unknown } | null)?.nonce;
    const expected = this.jobs.get(job_id);
    if (expected === undefined) throw new PlannerError('not_found', 'This test job is unknown.');
    if (
      typeof nonce !== 'string' ||
      nonce.length !== expected.length ||
      !timingSafeEqual(Buffer.from(nonce), Buffer.from(expected))
    )
      throw conflict('The test job answer does not match.');
    this.jobs.delete(job_id);
    return { answered: true };
  }

  /** One database write takes the server: the next move number, its address and the home mark. */
  commit(machine_id: string, body: unknown): ServerMoveCommitReply {
    const marker = this.require_target(machine_id);
    if ((body as { move_id?: unknown } | null)?.move_id !== marker.move_id)
      throw conflict('This commit names another move.');
    const next = this.options.database.write((tx) => {
      let state: 'commit' | 'done';
      try {
        state = held_commit_state(marker, read_server_location(tx));
      } catch (error) {
        throw conflict(error instanceof Error ? error.message : String(error));
      }
      if (state === 'commit') {
        if (!tx.get('SELECT 1 FROM machines WHERE machine_id=?', [marker.target_machine_id]))
          throw conflict('The target machine is not in the copied record.');
        tx.run('UPDATE server_link SET move_number=?, server_url=? WHERE singleton=1', [
          marker.move_number + 1,
          marker.server_url,
        ]);
        tx.run('UPDATE machines SET is_home=CASE WHEN machine_id=? THEN 1 ELSE 0 END', [
          marker.target_machine_id,
        ]);
      }
      return read_server_location(tx)!;
    });
    this.release(marker, true);
    return { server_id: next.server_id, move_number: next.move_number };
  }

  /**
   * Remove the marker, then reopen the barrier and conversation admission. The normal work
   * timer of the server services starts normal work once the barrier is open.
   */
  private release(marker: ServerMoveHeldMarker, reopen_conversation: boolean): void {
    const { data_directory, update_barrier } = this.options;
    try {
      unlinkSync(join(data_directory, SERVER_MOVE_HELD_FILE));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    const directory = openSync(data_directory, 'r');
    try {
      fsyncSync(directory);
    } finally {
      closeSync(directory);
    }
    this.marker = null;
    this.jobs.clear();
    if (update_barrier.move_hold === marker.move_id) {
      update_barrier.move_hold = null;
      update_barrier.reopen();
    }
    if (reopen_conversation) this.options.conversation()?.reopen_after_update();
  }
}
