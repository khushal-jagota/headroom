import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, realpath, rename, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { command_input_reader, descriptor_hash } from './files.ts';

test('a home reads the same command input from digest to delivery after the file is replaced', async () => {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'command-input-')));
  try {
    const folder = join(root, 'conversation'),
      file = join(folder, 'input');
    await mkdir(folder);
    const original = Buffer.from('saved command input');
    await writeFile(file, original);
    const reader = await command_input_reader(file);
    try {
      expect(await descriptor_hash(reader)).toEqual([
        createHash('sha256').update(original).digest('hex'),
        original.length,
      ]);
      await rename(file, join(folder, 'saved'));
      await writeFile(file, 'replacement');
      const bytes = Buffer.alloc(original.length);
      expect((await reader.read(bytes, 0, bytes.length, 0)).bytesRead).toBe(bytes.length);
      expect(bytes).toEqual(original);
    } finally {
      await reader.close();
    }
    await symlink(file, join(folder, 'link'));
    await expect(command_input_reader(join(folder, 'link'))).rejects.toMatchObject({
      code: 'ELOOP',
    });
    await symlink(folder, join(root, 'alias'));
    await expect(command_input_reader(join(root, 'alias/input'))).rejects.toThrow();
    await expect(command_input_reader(folder)).rejects.toThrow('artifact is not regular file');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
