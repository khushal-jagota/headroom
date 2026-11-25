import { cp, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { compareSignedPayload } from './promote_mac.ts';

test('promotion allows only the build identity bytes in the runtime marker', async () => {
  const work = await mkdtemp(join(tmpdir(), 'mac-promotion-'));
  const before = join(work, 'before'),
    after = join(work, 'after');
  const original = '{"application_revision":"ab","build_id":"old"}\n';
  try {
    await mkdir(before);
    await writeFile(join(before, 'mac-runtime.json'), original);
    await writeFile(join(before, 'manifest.json'), '{"source_digest":"qualified"}');
    await cp(before, after, { recursive: true });
    await writeFile(join(after, 'mac-runtime.json'), original.replace('old', 'new'));
    await compareSignedPayload(before, after, 'old', 'new');
    await writeFile(join(after, 'mac-runtime.json'), original.replace('old', 'new').trimEnd());
    await expect(compareSignedPayload(before, after, 'old', 'new')).rejects.toThrow();
    await writeFile(join(after, 'mac-runtime.json'), original.replace('old', 'new'));
    await writeFile(join(after, 'manifest.json'), '{"source_digest":"different"}');
    await expect(compareSignedPayload(before, after, 'old', 'new')).rejects.toThrow(
      /manifest.json/,
    );
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});
