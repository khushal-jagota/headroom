import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import { RETIRED_DRIVER_FILES, write_retired_driver_files } from './retired_driver_files.ts';

it('writes each retired driver file as an empty regular file', async () => {
  const root = await mkdtemp(join(tmpdir(), 'retired-driver-files-'));
  try {
    await write_retired_driver_files(root);
    for (const name of RETIRED_DRIVER_FILES) {
      expect((await stat(join(root, name))).isFile()).toBe(true);
      expect(await readFile(join(root, name), 'utf8')).toBe('');
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
