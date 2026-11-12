import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { registerReadFunction } from '../core/database/index.ts';
import type { ReadSql } from '../core/database/contracts.ts';
// The catalog needs Python-equivalent Unicode rules inside bounded SQL windows.
// A retained registration must not keep a callback alive after its read snapshot.
test('catalog SQL functions follow the lifetime of their read snapshot', () => {
  const database=fixture();
  let retained: ReadSql;
  database.read(sql => {
    retained=sql;
    registerReadFunction(sql,'artifact_stem_key',value => String(value).toLowerCase());
    expect(sql.get<{stem:string}>("SELECT artifact_stem_key('Ä') AS stem")?.stem).toBe('ä');
  });
  expect(() => registerReadFunction(retained!,'other',() => 'bad')).toThrow('scope');
  expect(() => database.read(sql => sql.get("SELECT artifact_stem_key('B')"))).toThrow('scope');
});
// Preview writes have a shorter existing lock budget. A rejected write must not
// leave that budget on the shared writer used by another domain.
test('preview write timeout restores the shared writer after success and failure', () => {
  const database=fixture();
  const timeout=(sql:ReadSql) => sql.get<{timeout:number}>('SELECT timeout FROM pragma_busy_timeout')!.timeout;
  expect(database.write(timeout,{busyTimeoutMs:250})).toBe(250);
  expect(database.write(timeout)).toBe(5000);
  expect(() => database.write(sql => {
    expect(timeout(sql)).toBe(250);
    throw new Error('rollback');
  },{busyTimeoutMs:250})).toThrow('rollback');
  expect(database.write(timeout)).toBe(5000);
});
