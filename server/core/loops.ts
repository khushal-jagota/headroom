/** Worker polling composition. Other loop owners attach through the lifecycle unit. */
import type { ChangeSignal } from './changes/index.ts';
import {
  WorkerStepReadinessLoop,
  type WorkerStepStartOptions,
} from '../runtime/worker_step_readiness_loop.ts';

export class BackgroundLoops {
  private unsubscribe: (() => void) | undefined;
  readonly worker_step_readiness_loop: WorkerStepReadinessLoop | null;
  constructor(loop: WorkerStepReadinessLoop | null) {
    this.worker_step_readiness_loop = loop;
  }
  start(tick_seconds: number, changes: ChangeSignal): void {
    if (!this.worker_step_readiness_loop) return;
    this.worker_step_readiness_loop.start(tick_seconds);
    this.unsubscribe = changes.subscribe(() => this.worker_step_readiness_loop!.wake());
  }
  async stop(): Promise<void> {
    this.unsubscribe?.();
    this.unsubscribe = undefined;
    await this.worker_step_readiness_loop?.stop();
  }
}

export function prepare_background_loops(options: {
  enable_worker_readiness: boolean;
  test_mode: boolean;
  dispatch_enabled: boolean;
  dispatcher_owned: boolean;
  worker: WorkerStepStartOptions | null;
  before_poll?: () => boolean;
}): BackgroundLoops {
  if (
    !options.enable_worker_readiness ||
    options.test_mode ||
    !options.dispatch_enabled ||
    !options.dispatcher_owned
  ) {
    return new BackgroundLoops(null);
  }
  if (options.worker === null) throw new Error('Worker readiness requires a conversation system');
  return new BackgroundLoops(
    new WorkerStepReadinessLoop({ ...options.worker, before_poll: options.before_poll }),
  );
}
