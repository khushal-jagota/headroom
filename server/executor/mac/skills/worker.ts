import { parentPort } from 'node:worker_threads';
import { BackendSpawnFailed } from '../../../conversation/backends/contracts.ts';
import { install_native_skill_snapshot, cleanup_handles, owns_handles } from './installation.ts';
import type { SkillReply, SkillRequest } from './contracts.ts';
if (parentPort === null) throw new Error('Skill worker requires its owner port.');
const port = parentPort,
  cancellation = new AbortController();
let active = false,
  stopped = false;
function finish_stop(): void {
  try {
    cleanup_handles();
    port.close();
  } catch (error) {
    const value = error instanceof Error ? error : new Error(String(error));
    port.postMessage({
      cleanup_error: { name: value.name, message: value.message },
    } satisfies SkillReply);
  }
}
port.on('message', (request: SkillRequest) => {
  if ('stop' in request) {
    stopped = true;
    cancellation.abort();
    if (!active) finish_stop();
    return;
  }
  if (active || stopped) throw new Error('Skill worker received an unordered installation.');
  active = true;
  void install_native_skill_snapshot(request.install, cancellation.signal)
    .then(
      (generation) => port.postMessage({ id: request.id, generation } satisfies SkillReply),
      (error: unknown) => {
        const value = error instanceof Error ? error : new Error(String(error));
        port.postMessage({
          id: request.id,
          error: {
            name: value instanceof BackendSpawnFailed ? 'BackendSpawnFailed' : value.name,
            message: value.message,
            code: (value as NodeJS.ErrnoException).code,
          },
          fatal: owns_handles(),
        } satisfies SkillReply);
      },
    )
    .finally(() => {
      active = false;
      if (stopped) finish_stop();
    });
});
