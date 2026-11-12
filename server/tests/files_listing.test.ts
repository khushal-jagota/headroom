import { mkdtempSync, rmSync, writeFileSync, utimesSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { foldDirectory } from '../files/logic/listing.ts';
import { pythonJson } from '../files/catalog/inventory.ts';
// UTF-16 sorting reverses these two valid file names. Existing HTTP names exercise
// accents but do not cross the BMP boundary. No database or backend host is needed.
test('artifact order and catalog revision JSON retain Python Unicode ordering', async () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-file-order-'));
  const bmp = '\uE000',
    astral = '\u{10000}';
  try {
    for (const name of [astral, bmp]) {
      const file = join(root, name + '.txt');
      writeFileSync(file, 'content');
      utimesSync(file, 100, 100);
    }
    expect((await foldDirectory(root)).map((entry) => entry.name)).toEqual([
      bmp + '.txt',
      astral + '.txt',
    ]);
    expect(pythonJson({ [astral]: 2, [bmp]: 1 }, true)).toBe(`{"${bmp}": 1, "${astral}": 2}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
