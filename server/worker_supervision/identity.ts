/** New local custody always uses exact native birth evidence, including libproc on macOS. */
import type { LocalChildIdentity } from '../conversation/local_child_contracts.ts';
import { INSPECTION_SECONDS } from './config.ts';
import type { WorkerSupervisionRuntime } from './runtime.ts';

export function local_process_identity(
  pid: number,
  runtime: WorkerSupervisionRuntime,
): Promise<LocalChildIdentity | null> {
  return runtime.operation(async () => {
    try {
      const deadline = runtime.clock() + INSPECTION_SECONDS;
      const { host, primitives } = await runtime.context(deadline);
      const current = (await primitives.snapshot(deadline)).get(pid);
      if (!current || current.zombie) return null;
      return {
        pid,
        birth: current.birth,
        boot: host.boot,
        unit: null,
        supervision_record: null,
      };
    } catch (error) {
      console.error('Worker process identity inspection failed', pid, error);
      return null;
    }
  });
}
