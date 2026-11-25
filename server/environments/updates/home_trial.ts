import { readFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type { UpdateBarrier } from '../update_barrier.ts';
import { attempt_path, missing } from './attempts.ts';

/** A trial only observes fresh leases on the enrolled machine's local socket.
 * The first reply tells the machine to retry, without admitting any ordinary work.
 * Its next compatible lease from the same process proves it is still exchanging messages.
 */
export class HomeUpdateTrial {
  private replied_to: string | null = null;

  readonly attempt_id: string;
  private readonly barrier: UpdateBarrier;

  constructor(attempt_id: string, barrier: UpdateBarrier) {
    this.attempt_id = attempt_id;
    this.barrier = barrier;
  }

  get active(): boolean {
    return this.barrier.frozen && this.barrier.generation === 0;
  }

  observed(incarnation: string): void {
    if (this.active && this.replied_to === incarnation)
      this.barrier.prepared_home_attempt = this.attempt_id;
  }

  replied(incarnation: string): void {
    if (this.active) this.replied_to = incarnation;
  }

  get prepared_attempt_id(): string | null {
    return this.active && this.barrier.prepared_home_attempt === this.attempt_id
      ? this.attempt_id
      : null;
  }
}

export function read_home_trial(
  barrier: UpdateBarrier | undefined,
  revision: string | null,
): HomeUpdateTrial | null {
  if (!barrier?.frozen || !revision) return null;
  try {
    const attempt = JSON.parse(readFileSync(attempt_path(dirname(barrier.path)), 'utf8'));
    const owner = readFileSync(barrier.path, 'utf8').trim();
    if (
      attempt.state === 'installing' &&
      (attempt.to_sha === revision ||
        (attempt.from_sha === revision && attempt.steps?.rollback_app_restored !== undefined)) &&
      owner &&
      attempt.attempt_id === owner
    )
      return new HomeUpdateTrial(owner, barrier);
    return null;
  } catch (error) {
    if (missing(error)) return null;
    throw error;
  }
}
