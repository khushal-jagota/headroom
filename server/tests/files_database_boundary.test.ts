import { expect, test } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { registerReadFunction } from '../core/database/index.ts';
import type { ReadSql } from '../core/database/contracts.ts';
// The catalog needs Python-equivalent Unicode rules inside bounded SQL windows.
// A retained registration must not keep a callback alive after its read snapshot.
test('catalog SQL functions follow the lifetime of their read snapshot', () => {
  const database = fixture();
  let retained: ReadSql;
  database.read((sql) => {
    retained = sql;
    registerReadFunction(sql, 'artifact_stem_key', (value) => String(value).toLowerCase());
    expect(sql.get<{ stem: string }>("SELECT artifact_stem_key('Ä') AS stem")?.stem).toBe('ä');
  });
  expect(() => registerReadFunction(retained!, 'other', () => 'bad')).toThrow('scope');
  expect(() => database.read((sql) => sql.get("SELECT artifact_stem_key('B')"))).toThrow('scope');
  database.read((sql) => {
    registerReadFunction(sql, 'artifact_stem_key', () => 'replacement');
    expect(sql.get<{ stem: string }>("SELECT artifact_stem_key('B') AS stem")?.stem).toBe(
      'replacement',
    );
  });
});
