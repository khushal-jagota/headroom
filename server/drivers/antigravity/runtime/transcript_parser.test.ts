import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
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
const usage = (request_id: string): number[] => [
  ...num(1, 1402),
  ...num(2, 100),
  ...num(3, 30),
  ...num(4, 40),
  ...num(5, 500),
  ...num(9, 25),
  ...num(10, 15),
  ...text(11, request_id),
];
// A step: usage at 9, model at 24 (id 1, name at 12), timestamp at 8.
const step = (request_id: string): Buffer =>
  Buffer.from([
    ...bytes(9, usage(request_id)),
    ...bytes(24, [...num(1, 1402), ...text(12, 'Claude 4.5 Sonnet')]),
    ...bytes(8, num(1, SECONDS)),
  ]);

describe('antigravity transcript parser', () => {
  let dir: string;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'agy-usage-'));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  function make_database(name: string): string {
    const path = join(dir, name);
    const db = new BetterSqlite3(path);
    db.exec(
      'CREATE TABLE gen_metadata(idx INTEGER, data BLOB); CREATE TABLE steps(idx INTEGER, metadata BLOB); CREATE TABLE trajectory_metadata_blob(data BLOB);',
    );
    const insert = db.prepare('INSERT INTO steps(idx, metadata) VALUES (?, ?)');
    insert.run(0, step('req-1'));
    insert.run(1, step('req-1'));
    db.close();
    return path;
  }

  it('decodes one usage step and drops its duplicate', async () => {
    const records = await parser.read_file(make_database('session-a.db'));
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

  it('names a model by id and strips the effort and Argon variant suffixes for pricing', () => {
    expect(antigravity_rate_model('gemini-3.8-flash-high')).toBe('gemini/gemini-3.8-flash');
    expect(antigravity_rate_model('gemini-4-argon')).toBe('gemini/gemini-4-argon');
    expect(antigravity_rate_model('gemini-4-argon-trial')).toBe('gemini/gemini-4-argon');
    expect(antigravity_rate_model('gemini-4-argon-g')).toBe('gemini/gemini-4-argon');
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
    expect(await parser.read_file(text_file)).toEqual([]);
    expect(await parser.read_file(corrupt)).toEqual([]);
    expect(await parser.read_file(join(dir, 'missing.db'))).toEqual([]);
  });

  it('refingerprints a database when its write-ahead log changes', () => {
    const path = make_database('session-b.db');
    const before = parser.fingerprint(path, statSync(path));
    writeFileSync(`${path}-wal`, 'x');
    const after = parser.fingerprint(path, statSync(path));
    expect(after).not.toBe(before);
    expect(parser.fingerprint(join(dir, 'a.txt'), statSync(path))).toBe(
      parser.fingerprint(join(dir, 'b.meta'), statSync(path)),
    );
  });
});
