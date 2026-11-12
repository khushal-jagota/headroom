/** A failed initializer must never leave a format that a later opener admits. */
import { expect, test } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { assertDatabaseVersion, createDurableSidecarDatabase } from './index.ts';

test('a failed fresh initializer leaves version zero incompatible', () => {
  const root = mkdtempSync(join(tmpdir(), 'panels-durable-sidecar-')),
    path = join(root, 'spool.sqlite3');
  try {
    expect(() =>
      createDurableSidecarDatabase(path, {
        schemaVersion: 5,
        foreignKeys: true,
        refusalMessage: 'unsupported spool format',
        initialize(tx) {
          tx.run('CREATE TABLE discarded(value)');
          throw new Error('initializer failed');
        },
      }),
    ).toThrow('initializer failed');
    expect(() => assertDatabaseVersion(path, 5, 'unsupported spool format')).toThrow(
      'unsupported spool format',
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
