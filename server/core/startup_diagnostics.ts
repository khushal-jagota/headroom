import { writeSync } from 'node:fs';

/** Fixed phase labels expose stalled startup without configuration or record contents. */
export function begin_startup_phase(name: string): () => void {
  const began = performance.now();
  const write = (state: 'start' | 'end') => {
    try {
      writeSync(
        2,
        `Panels startup ${process.pid}: ${name} ${state} ${(performance.now() - began).toFixed(1)}ms\n`,
      );
    } catch {
      /* Diagnostics cannot change startup or cleanup outcomes. */
    }
  };
  write('start');
  return () => write('end');
}
