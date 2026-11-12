/** One manual synthetic 2GiB check. Export only; the caller owns execution and evidence. */
import { closeSync, mkdtempSync, openSync, realpathSync, rmSync, ftruncateSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { FilesService } from '../files.ts';

export async function large_file_probe(parent = tmpdir()): Promise<unknown> {
  const root = realpathSync(mkdtempSync(join(parent, 'mac-files-2g-')));
  const source = join(root, 'source'),
    target = join(root, 'target'),
    bytes = 2 * 1024 ** 3;
  const descriptor = openSync(source, 'wx', 0o600);
  try {
    ftruncateSync(descriptor, bytes);
  } finally {
    closeSync(descriptor);
  }
  const service = new FilesService(),
    interval_ms = 10,
    lateness: number[] = [];
  let next = performance.now() + interval_ms,
    max_rss = process.memoryUsage().rss;
  const timer = setInterval(() => {
    const now = performance.now();
    lateness.push(Math.max(0, now - next));
    next = now + interval_ms;
    max_rss = Math.max(max_rss, process.memoryUsage().rss);
  }, interval_ms);
  const started = performance.now();
  try {
    const [sha, count] = await service.file_digest(source);
    if (count !== bytes) throw new Error('the synthetic source length differs');
    await service.copy_verified(source, target, sha, count);
    const verified = await service.verified_version(target, sha, count);
    if (verified === null) throw new Error('the synthetic copy differs');
    await service.stop({ deadline: performance.now() / 1000 + 30 });
    await service.settled();
    const sorted = [...lateness].sort((a, b) => a - b);
    return {
      bytes,
      sha256: sha,
      seconds: (performance.now() - started) / 1000,
      interval_ms,
      timer_ticks: sorted.length,
      max_lateness_ms: sorted.at(-1) ?? null,
      p99_lateness_ms: sorted[Math.floor(sorted.length * 0.99)] ?? null,
      max_lateness_budget_ms: 50,
      within_budget: sorted.length > 0 && sorted.at(-1)! <= 50,
      max_process_rss_mib: max_rss / 1024 ** 2,
      checks: [
        'full source hash',
        'full verified copy',
        'full target rehash',
        'owned worker stop and settlement',
      ],
    };
  } finally {
    clearInterval(timer);
    try {
      await service.stop({ deadline: performance.now() / 1000 + 30 });
    } finally {
      // A deadline refusal is not proof that the worker stopped touching its files.
      await service.settled();
      rmSync(root, { recursive: true, force: true });
    }
  }
}
