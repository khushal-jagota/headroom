import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { expect, test } from 'vitest';
import { acquireLock, acquireRuntimeOwnership } from '../core/locks/index.ts';
// A fake lock cannot prove interoperability with Python's fcntl.flock.
// The two cases use private files and stdlib Python, without a server or planner import.
test('Python ownership excludes Node until the Python owner exits', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-lock-py-')),
    db = join(dir, 'db');
  writeFileSync(db, '');
  const child = spawn(
    'python3',
    [
      '-u',
      '-c',
      "import fcntl,sys;f=open(sys.argv[1] + '.conversation-owner.lock','a+b');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);print('ready',flush=True);sys.stdin.read()",
      db,
    ],
    { stdio: ['pipe', 'pipe', 'pipe'] },
  );
  try {
    await once(child.stdout, 'data');
    expect(() => acquireRuntimeOwnership(db)).toThrow('ConversationRuntimeAlreadyOwned');
    child.stdin.end();
    await once(child, 'exit');
    const handle = acquireRuntimeOwnership(db);
    handle.close();
  } finally {
    if (child.exitCode === null) {
      child.stdin.end();
      child.kill();
      await once(child, 'exit');
    }
    rmSync(dir, { recursive: true, force: true });
  }
});
test('Node ownership excludes Python and dispatcher contention returns false', () => {
  const dir = mkdtempSync(join(tmpdir(), 'panels-lock-node-')),
    db = join(dir, 'db');
  writeFileSync(db, '');
  const handle = acquireRuntimeOwnership(db);
  const probe =
    "import fcntl,sys;f=open(sys.argv[1],'a+b');\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(23)";
  try {
    expect(() =>
      execFileSync('python3', ['-c', probe, `${db}.conversation-owner.lock`], { stdio: 'pipe' }),
    ).toThrow();
    expect(acquireLock(`${db}.conversation-owner.lock`)).toBeNull();
    handle.close();
    expect(() =>
      execFileSync('python3', ['-c', probe, `${db}.conversation-owner.lock`], { stdio: 'pipe' }),
    ).not.toThrow();
  } finally {
    handle.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
