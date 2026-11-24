import {
  mkdtemp,
  mkdir,
  writeFile,
  symlink,
  readFile,
  lstat,
  rm,
  readdir,
  readlink,
  rename,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { clear_abandoned_executor_uploads } from './upload_cleanup.ts';

test('boot cleanup archives all machine uploads across starts and never follows a link', async () => {
  const root = await mkdtemp(join(tmpdir(), 'panels-upload-cleanup-'));
  try {
    const uploads = join(root, 'executor-uploads');
    const retained = join(root, 'served-file');
    await writeFile(retained, 'retained');
    await mkdir(join(uploads, 'machine'), { recursive: true });
    await writeFile(join(uploads, 'machine', '.incoming-abandoned'), 'partial');
    await writeFile(join(uploads, 'complete'), 'complete');
    await symlink(retained, join(uploads, 'link'));
    await clear_abandoned_executor_uploads(join(root, 'planning.db'));
    await expect(lstat(uploads)).rejects.toMatchObject({ code: 'ENOENT' });
    const archive = join(root, 'executor-upload-archive');
    const [first] = await readdir(archive);
    expect(await readFile(join(archive, first, 'machine', '.incoming-abandoned'), 'utf8')).toBe(
      'partial',
    );
    expect(await readFile(join(archive, first, 'complete'), 'utf8')).toBe('complete');
    expect(await readlink(join(archive, first, 'link'))).toBe(retained);
    expect(await readFile(retained, 'utf8')).toBe('retained');
    await clear_abandoned_executor_uploads(join(root, 'planning.db'));
    expect(await readdir(archive)).toEqual([first]);
    await mkdir(uploads);
    await writeFile(join(uploads, 'later'), 'later transfer');
    // Transfers admitted after boot remain in the new upload area until the next boot.
    expect(await readFile(join(uploads, 'later'), 'utf8')).toBe('later transfer');
    await clear_abandoned_executor_uploads(join(root, 'planning.db'));
    const runs = await readdir(archive);
    expect(runs).toHaveLength(2);
    const second = runs.find((name) => name !== first)!;
    expect(await readFile(join(archive, second, 'later'), 'utf8')).toBe('later transfer');
    expect(await readFile(join(archive, first, 'complete'), 'utf8')).toBe('complete');
    await symlink(root, uploads);
    await expect(clear_abandoned_executor_uploads(join(root, 'planning.db'))).rejects.toThrow(
      'not a link',
    );
    expect(await readFile(retained, 'utf8')).toBe('retained');
    await rm(uploads);
    await mkdir(uploads);
    await writeFile(join(uploads, 'untouched'), 'keep');
    await rename(archive, join(root, 'retained-archive'));
    await symlink(join(root, 'retained-archive'), archive);
    await expect(clear_abandoned_executor_uploads(join(root, 'planning.db'))).rejects.toThrow(
      'archive must be a directory',
    );
    expect(await readFile(join(uploads, 'untouched'), 'utf8')).toBe('keep');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
