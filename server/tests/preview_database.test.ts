import { expect, test } from 'vitest';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { createDatabase } from '../core/database/index.ts';
import type { ReadSql } from '../core/database/contracts.ts';
// Preview writes have a shorter existing lock budget. A rejected write must not
// leave that budget on the shared writer used by another domain.
test('preview write timeout restores the shared writer after success and failure', () => {
  const database = fixture();
  const timeout = (sql: ReadSql) =>
    sql.get<{ timeout: number }>('SELECT timeout FROM pragma_busy_timeout')!.timeout;
  expect(database.write(timeout, { busyTimeoutMs: 250 })).toBe(250);
  expect(database.write(timeout)).toBe(5000);
  expect(() =>
    database.write(
      (sql) => {
        expect(timeout(sql)).toBe(250);
        throw new Error('rollback');
      },
      { busyTimeoutMs: 250 },
    ),
  ).toThrow('rollback');
  expect(database.write(timeout)).toBe(5000);
});

test('preview timeout restores the writer when a commit step throws', () => {
  const base = fixture();
  const path = fixture_path(base);
  base.close();
  let fail = true;
  const database = createDatabase(path, {
    commitSteps: [
      () => {
        if (fail) throw new Error('projection failed');
      },
    ],
  });
  try {
    expect(() =>
      database.write((sql) => sql.run('UPDATE projects SET name=name'), { busyTimeoutMs: 250 }),
    ).toThrow('projection failed');
    fail = false;
    expect(
      database.write(
        (sql) => sql.get<{ timeout: number }>('SELECT timeout FROM pragma_busy_timeout')!.timeout,
      ),
    ).toBe(5000);
  } finally {
    database.close();
  }
});
