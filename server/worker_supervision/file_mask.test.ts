/** Real descendants prove mask inheritance without a backend or platform service host. */
import { mkdtemp, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { ChildProcesses, OwnedProcess } from '../lifecycle/owned_processes.ts';
import { with_worker_file_mask } from './file_mask.ts';

test('backend and descendant output use 0022 while machine state retains 0077', async () => {
  const root = await mkdtemp(join(tmpdir(), 'worker-file-mask-'));
  const children = new ChildProcesses();
  const previous = process.umask(0o077);
  const output = `
    const fs = require('node:fs');
    const path = require('node:path');
    const root = process.argv[1];
    fs.writeFileSync(path.join(root, 'file'), 'output');
    fs.mkdirSync(path.join(root, 'directory'));
  `;
  try {
    const child = with_worker_file_mask(() =>
      children.spawn(
        process.execPath,
        [
          '-e',
          `
      const fs = require('node:fs');
      const path = require('node:path');
      const root = process.argv[1];
      fs.mkdirSync(path.join(root, 'backend'));
      fs.mkdirSync(path.join(root, 'descendant'));
      fs.writeFileSync(path.join(root, 'backend/file'), 'output');
      fs.mkdirSync(path.join(root, 'backend/directory'));
      const result = require('node:child_process').spawnSync(process.execPath, ['-e', ${JSON.stringify(output)}, path.join(root, 'descendant')]);
      process.exit(result.status ?? 1);
    `,
          root,
        ],
        {},
      ),
    );
    expect(process.umask()).toBe(0o077);
    const owned = new OwnedProcess(child);
    await owned.exited;
    expect(child.exitCode).toBe(0);
    for (const name of ['backend', 'descendant']) {
      expect((await stat(join(root, name, 'file'))).mode & 0o777).toBe(0o644);
      expect((await stat(join(root, name, 'directory'))).mode & 0o777).toBe(0o755);
    }
    expect(() =>
      with_worker_file_mask(() => {
        throw new Error('refused');
      }),
    ).toThrow('refused');
    expect(process.umask()).toBe(0o077);
  } finally {
    process.umask(previous);
    children.seal();
    await children.settled();
    await rm(root, { recursive: true, force: true });
  }
});
