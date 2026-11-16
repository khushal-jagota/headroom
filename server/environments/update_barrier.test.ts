import { expect, test } from 'vitest';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { UpdateBarrier, with_installer_lifetime } from './update_barrier.ts';

test('retains admitted saves and rejects a stale durable cut after cancellation', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-update-cut-'));
  try {
    const barrier = new UpdateBarrier(join(root, 'update-barrier.json'));
    const save = barrier.operation();
    const cut = barrier.close();
    barrier.persist('first', cut);
    expect(new UpdateBarrier(barrier.path).frozen).toBe(true);
    expect(barrier.operation().admitted).toBe(false);
    let drained = false;
    const drain = barrier.drain().then(() => {
      drained = true;
    });
    await Promise.resolve();
    expect(drained).toBe(false);
    save.release();
    await drain;
    const canceled = barrier.reopen();
    barrier.refresh();
    expect(barrier.frozen).toBe(false);
    barrier.remove(canceled);
    barrier.freeze('second');
    barrier.persist('first', cut);
    barrier.remove(canceled);
    expect(readFileSync(barrier.path, 'utf8')).toBe('second\n');
    expect(barrier.reopen_if_current(cut)).toBeNull();
    barrier.release();
    expect(new UpdateBarrier(barrier.path).frozen).toBe(false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('uses the actual installer lock and preserves a successor marker on terminal cleanup', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-update-owner-'));
  try {
    const barrier = new UpdateBarrier(join(root, 'update-barrier.json'));
    barrier.freeze('old');
    let finish!: () => void;
    const held = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const installer = with_installer_lifetime(root, 'old', async () => {
      await held;
      return 0;
    });
    expect(barrier.installer_active('unrelated-record-text')).toBe(true);
    expect(await with_installer_lifetime(root, 'new', async () => 1)).toEqual({ acquired: false });
    barrier.freeze('new');
    finish();
    expect(await installer).toEqual({ acquired: true, result: 0 });
    expect(barrier.installer_active('old')).toBe(false);
    expect(readFileSync(barrier.path, 'utf8')).toBe('new\n');
    await with_installer_lifetime(root, 'new', async () => 0);
    barrier.refresh();
    expect(barrier.frozen).toBe(false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
