import { spawnSync } from 'node:child_process';
import { expect, test } from 'vitest';
import {
  JsonNumber,
  clone_numeric_json,
  parse_numeric_json,
  stringify_python_json,
} from '../core/http/numeric_json.ts';
import { prepare_plain_document } from '../managers/logic/documents.ts';

// JSON.parse normally erases float token kinds and rounds large integers. Existing
// HTTP comparisons construct JSON from ordinary objects and cannot prove raw tokens.
// One stdlib Python process is the byte oracle for this table; no server or DB starts.
test('native numeric-kind intake matches Python bytes and never exposes marker objects', () => {
  const documents = [
    '0',
    '900719925474099312345',
    '-0',
    '-0.0',
    '50.0',
    '1e5',
    '1E+22',
    '1e-7',
    '0.1',
    '0.123456789012345678901234567890',
    '{"nested":[900719925474099312345,-0,50.0,1e5,1E+22,1e-7,0.1,0.123456789012345678901234567890]}',
    '{"kind":"headroom.page","version":1,"title":"Numbers ☃","summary":"","blocks":[{"type":"table","id":"table","columns":["A","B"],"rows":[["A","B"]],"column_widths":[50.0,50.0]}]}',
  ];
  const oracle = spawnSync(
    'python3',
    [
      '-c',
      `import json,sys
print(json.dumps([{'spaced':json.dumps(json.loads(raw),ensure_ascii=False),'compact':json.dumps(json.loads(raw),ensure_ascii=False,separators=(',',':')),'length':len(json.dumps(json.loads(raw),ensure_ascii=False))} for raw in json.load(sys.stdin)],ensure_ascii=False))`,
    ],
    { input: JSON.stringify(documents), encoding: 'utf8' },
  );
  expect(oracle.status, oracle.stderr).toBe(0);
  const answers = JSON.parse(oracle.stdout) as {
    spaced: string;
    compact: string;
    length: number;
  }[];
  for (const [index, raw] of documents.entries()) {
    const value = parse_numeric_json(raw);
    expect(stringify_python_json(value), raw).toBe(answers[index]!.spaced);
    expect(Array.from(stringify_python_json(value)).length, raw).toBe(answers[index]!.length);
    expect(JSON.stringify(value), raw).toBe(answers[index]!.compact);
    expect(stringify_python_json(clone_numeric_json(value)), raw).toBe(answers[index]!.spaced);
    expect(stringify_python_json(parse_numeric_json(stringify_python_json(value))), raw).toBe(
      answers[index]!.spaced,
    );
  }
  const page = prepare_plain_document(parse_numeric_json(documents.at(-1)!), () => 'unused');
  expect(stringify_python_json(page)).toBe(answers.at(-1)!.spaced);
  expect((parse_numeric_json('50.0') as JsonNumber).kind).toBe('float');
  expect((parse_numeric_json('-0') as JsonNumber).kind).toBe('integer');
  expect(() =>
    prepare_plain_document(
      parse_numeric_json(documents.at(-1)!.replace('"version":1', '"version":1.0')),
      () => 'unused',
    ),
  ).toThrow('unsupported value');
});
