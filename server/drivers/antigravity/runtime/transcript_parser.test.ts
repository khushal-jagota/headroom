import { mkdirSync, mkdtempSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import BetterSqlite3 from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { lookupRate, parseRateTable } from '../../../conversation/usage/usagePricing.ts';
import parser, { antigravity_rate_model } from './transcript_parser.ts';

const varint = (value: number): number[] => {
  const out: number[] = [];
  let rest = value;
  while (rest >= 128) {
    out.push((rest % 128) | 128);
    rest = Math.floor(rest / 128);
  }
  return [...out, rest];
};
const num = (field: number, value: number): number[] => [...varint(field * 8), ...varint(value)];
const bytes = (field: number, body: number[]): number[] => [
  ...varint(field * 8 + 2),
  ...varint(body.length),
  ...body,
];
const text = (field: number, value: string): number[] =>
  bytes(field, [...new TextEncoder().encode(value)]);

const SECONDS = 1_700_000_000;
const usage = (request_id: string, model_id: number): number[] => [
  ...num(1, model_id),
  ...num(2, 100),
  ...num(3, 30),
  ...num(4, 40),
  ...num(5, 500),
  ...num(9, 25),
  ...num(10, 15),
  ...text(11, request_id),
];
// A step: usage at 9, model at 24 (id 1, name at 12), timestamp at 8. No name: only the id.
const step = (
  request_id: string,
  model_id = 1402,
  name = 'Claude 4.5 Sonnet',
  seconds = SECONDS,
): Buffer =>
  Buffer.from([
    ...bytes(9, usage(request_id, model_id)),
    ...bytes(24, [...num(1, model_id), ...(name ? text(12, name) : [])]),
    ...bytes(8, num(1, seconds)),
  ]);
const TABLES =
  'CREATE TABLE gen_metadata(idx INTEGER, data BLOB); CREATE TABLE steps(idx INTEGER, metadata BLOB); CREATE TABLE trajectory_metadata_blob(data BLOB);';

describe('antigravity transcript parser', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'agy-usage-'));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  function make_database(name: string, steps = [step('req-1'), step('req-1')]): string {
    const path = join(dir, name);
    const db = new BetterSqlite3(path);
    db.exec(TABLES);
    const insert = db.prepare('INSERT INTO steps(idx, metadata) VALUES (?, ?)');
    for (const [idx, metadata] of steps.entries()) insert.run(idx, metadata);
    db.close();
    return path;
  }
  /** A conversation agy is still writing: its rows sit in the write-ahead log, beside `-wal` and
   * `-shm`, and the database file itself was last written three days ago. */
  function open_writing_database(name: string) {
    const path = join(dir, name);
    const writer = new BetterSqlite3(path);
    writer.pragma('journal_mode = WAL');
    writer.exec(TABLES);
    const insert = writer.prepare('INSERT INTO steps(idx, metadata) VALUES (?, ?)');
    insert.run(0, step('req-1', 1402, 'Claude 4.5 Sonnet', Date.now() / 1000));
    const three_days_ago = Date.now() / 1000 - 3 * 24 * 60 * 60;
    utimesSync(path, three_days_ago, three_days_ago);
    return { path, writer, insert };
  }
  const window_start = () => Date.now() - 36 * 60 * 60 * 1000;

  it('decodes one usage step and drops its duplicate', async () => {
    const { records } = await parser.read_file(make_database('session-a.db'));
    expect(records).toEqual([
      {
        provider: 'antigravity',
        sessionId: 'session-a',
        timestampMs: SECONDS * 1000,
        model: 'claude-sonnet-4-5',
        totals: {
          uncachedInputTokens: 100,
          cachedInputTokens: 500,
          cacheCreationTokens: 40,
          outputTokens: 40,
          reasoningTokens: 25,
        },
        reportedCostUsd: null,
        dedupeKey: 'antigravity:11:req-1',
      },
    ]);
  });

  // Risk: a recorded variant name, such as one with two suffixes, prices at nothing.
  it('prices recorded variants as their base model', () => {
    expect(
      Object.fromEntries(
        [
          'gemini-3.8-flash-high',
          'gemini-4-hydrogen-tiered-xhigh',
          'gemini-4-argon-trial',
          'gemini-4-argon-g',
        ].map((model) => [model, antigravity_rate_model(model)]),
      ),
    ).toEqual({
      'gemini-3.8-flash-high': 'gemini/gemini-3.8-flash',
      'gemini-4-hydrogen-tiered-xhigh': 'gemini/gemini-4-hydrogen',
      'gemini-4-argon-trial': 'gemini/gemini-4-argon',
      'gemini-4-argon-g': 'gemini/gemini-4-argon',
    });
  });

  // Risk: a model id only another conversation names stays unnamed and unpriced, including when
  // this conversation's read comes back from the scan cache.
  it('names a model id from another database', async () => {
    const unnamed = await parser.read_file(make_database('unnamed.db', [step('req-1', 1999, '')]));
    const named = await parser.read_file(
      make_database('named.db', [step('req-2', 1999, 'Gemini 9 Pro')]),
    );
    expect(parser.finish([unnamed])[0]!.map((record) => record.model)).toEqual([
      'antigravity-model-1999',
    ]);
    const cached = {
      records: unnamed.records,
      state: parser.revive_state(JSON.parse(JSON.stringify(unnamed.state)))!,
    };
    expect(
      parser.finish([cached, named])[0]!.map(({ model, pricing_model }) => [model, pricing_model]),
    ).toEqual([['gemini-9-pro', 'gemini/gemini-9-pro']]);
  });

  // Risk: a model agy offers prices as unpriced, or at a reseller's rate. The entries are copied
  // verbatim from LiteLLM's table of 18 Nov 2026, including the resellers that make the bare
  // Argon and gpt-oss names ambiguous, so the table drops those aliases as the real one does.
  it('prices every model agy 1.5.10 offers at Google’s or Anthropic’s own rate', () => {
    const table = parseRateTable(
      JSON.parse(
        readFileSync(new URL('./testing/litellm_entries_2026-11-18.json', import.meta.url), 'utf8'),
      ),
    );
    const offered = (
      JSON.parse(
        readFileSync(new URL('./testing/agy_1.5.10_session_new.json', import.meta.url), 'utf8'),
      ) as { models: { availableModels: { modelId: string }[] } }
    ).models.availableModels.map((entry) => entry.modelId);
    // Input and output, in USD per token: e-6 is per million.
    const rate_of = (model: string) => {
      const rate = lookupRate(table, antigravity_rate_model(model));
      return rate && [rate.inputCostPerToken, rate.outputCostPerToken];
    };
    const opus = [4e-6, 20e-6];
    const sonnet = [2e-6, 10e-6];
    const flash = [0.75e-6, 3.75e-6];
    const hydrogen = [1.5e-6, 7.5e-6];
    const argon = [2e-6, 10e-6];
    expect(Object.fromEntries(offered.map((model) => [model, rate_of(model)]))).toEqual({
      'gemini-4-argon-medium': argon,
      'gemini-4-argon-high': argon,
      'gemini-4-argon-low': argon,
      'gemini-4-hydrogen-high': hydrogen,
      'gemini-4-hydrogen-xhigh': hydrogen,
      'gemini-4-hydrogen-medium': hydrogen,
      'gemini-4-hydrogen-low': hydrogen,
      // An internal routing model with no public price.
      'gemini-pro-orchestrator-g1-pro': null,
      'gemini-3.8-flash-high': flash,
      'gemini-3.8-flash-medium': flash,
      'gemini-3.8-flash-low': flash,
      'claude-opus-5-5-low': opus,
      'claude-opus-5-5-medium': opus,
      'claude-opus-5-5-high': opus,
      'claude-sonnet-5-5-low': sonnet,
      'claude-sonnet-5-5-medium': sonnet,
      'claude-sonnet-5-5-high': sonnet,
      // Antigravity's hosting price is not published, and resellers' prices disagree.
      'gpt-oss-120b-medium': null,
    });
  });

  it('yields nothing for a file that is not a readable database', async () => {
    const text_file = join(dir, 'notes.txt');
    const corrupt = join(dir, 'broken.db');
    writeFileSync(text_file, 'hello');
    writeFileSync(corrupt, 'not sqlite at all, but long enough to look like a header');
    for (const path of [text_file, corrupt, join(dir, 'missing.db')])
      expect((await parser.read_file(path)).records).toEqual([]);
  });

  // Risk: rows that sit only in the write-ahead log of a database file older than the window
  // are dropped, or are not read again when the log grows.
  it('reads a conversation whose only change since the window start is in its log', async () => {
    const { path, writer, insert } = open_writing_database('writing.db');
    try {
      const [listed] = await parser.list_files(dir, window_start());
      expect(listed?.path).toBe(path);
      expect((await parser.read_file(path)).records).toHaveLength(1);
      insert.run(1, step('req-2', 1402, 'Claude 4.5 Sonnet', Date.now() / 1000));
      const [relisted] = await parser.list_files(dir, window_start());
      expect(relisted?.fingerprint).not.toBe(listed?.fingerprint);
      expect((await parser.read_file(path)).records).toHaveLength(2);
    } finally {
      writer.close();
    }
  });

  // Risk: a database's `-wal` and `-shm` companions, and other files, are listed as empty
  // transcripts and inflate the skipped count.
  it('lists only conversation databases', async () => {
    const { path, writer } = open_writing_database('writing.db');
    try {
      writeFileSync(join(dir, 'notes.txt'), 'hello');
      mkdirSync(join(dir, 'nested'));
      const nested = make_database('nested/inner.db');
      const listed = (await parser.list_files(dir, window_start())).map((file) => file.path);
      expect(listed.sort()).toEqual([nested, path].sort());
    } finally {
      writer.close();
    }
  });
});
