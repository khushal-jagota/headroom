import { mkdtemp, mkdir, writeFile, symlink, readFile, lstat, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { clear_abandoned_executor_uploads } from './upload_cleanup.ts';

test('boot cleanup clears all machine uploads and never follows an upload link', async () => {
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
    expect(await readFile(retained, 'utf8')).toBe('retained');
    await clear_abandoned_executor_uploads(join(root, 'planning.db'));
    await symlink(root, uploads);
    await expect(clear_abandoned_executor_uploads(join(root, 'planning.db'))).rejects.toThrow(
      'not a link',
    );
    expect(await readFile(retained, 'utf8')).toBe('retained');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
