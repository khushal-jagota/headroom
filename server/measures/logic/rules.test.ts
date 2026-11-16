import { describe, expect, it } from 'vitest';
import { realpathSync } from 'node:fs';
import type {
  CountPeriod,
  Definitions,
  FormulaExpression,
  Measure,
  MeasureKind,
  Observation,
  Observations,
  OutputMeasure,
} from '../contracts.ts';
import { pair_key } from '../contracts.ts';
import { combine_coverage, observation_coverage, validate_coverage } from './coverage.ts';
import { dependencies, parse_formula, validate_graph } from './formulas.ts';
import { local_date } from './dates.ts';
import {
  Evaluation,
  demanded_pairs,
  evaluate,
  expression_references,
  phase_changed,
} from './evaluation.ts';
const NOW = Date.parse('2026-10-27T12:00:00Z') / 1000;
const day = '2026-10-27';
const definition = (
  id: string,
  kind: MeasureKind = 'reading',
  period: CountPeriod | null = null,
): Measure => ({
  id,
  project_id: 'project_vylo',
  name: id,
  unit: 'items',
  origin: 'taken',
  kind,
  timezone: 'Europe/Berlin',
  period,
  source_id: 'source',
  formula: null,
  definition_revision: 1,
  created_at: NOW,
  updated_at: NOW,
  write_revision: 0,
});
const constant = (value: number): FormulaExpression => ({ op: 'constant', value });
const ref = (measure_id: string, day_offset = 0): FormulaExpression => ({
  op: 'reference',
  measure_id,
  day_offset,
});
const binary = (
  op: 'add' | 'subtract' | 'multiply' | 'divide' | 'power' | 'min' | 'max',
  left: FormulaExpression,
  right: FormulaExpression,
): FormulaExpression => ({ op, left, right });
const assembled = (id: string, formula: FormulaExpression): Measure => ({
  ...definition(id),
  origin: 'assembled',
  source_id: null,
  formula,
});
const observation = (measure_id: string, date: string, value: number): Observation => ({
  measure_id,
  date,
  value,
  run_id: 'run',
  observed_at: NOW,
  definition_revision: 1,
  period_interval: null,
  manual_write_id: null,
  query_start_at: null,
  query_end_at: null,
  limitation: null,
  coverage_status: 'not_applicable',
  coverage_reasons: [],
});
const definitions = (...m: Measure[]): Definitions => new Map(m.map((v) => [v.id, v]));
const observations = (...o: Observation[]): Observations => new Map(o.map((v) => [pair_key(v), v]));
describe('ported Python measure rules', () => {
  it.each([
    ['2026-10-25', 'day', 'Europe/Berlin', '2026-10-24T22:00:00Z', '2026-10-25T23:00:00Z'],
    ['2026-10-28', 'week', 'UTC', '2026-10-26T00:00:00Z', '2026-11-02T00:00:00Z'],
    ['2026-10-28', 'month', 'UTC', '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z'],
  ] as const)('captured local calendar bounds %s %s', (day, period, timezone, first, last) => {
    const m = { ...definition('count', 'period_count', period), timezone };
    const captured: OutputMeasure = {
      measure_id: m.id,
      definition_revision: 1,
      kind: m.kind,
      timezone,
      period,
      required_date: '2026-10-29',
      period_interval: null,
    };
    const start = Date.parse(first) / 1000,
      end = Date.parse(last) / 1000;
    expect(observation_coverage(m, captured, day, start, end, null)[0]).toBe('complete');
    for (const [a, b] of [
      [start + 1, end],
      [start, end - 1],
      [start - 1, end],
      [start, end + 1],
    ])
      expect(observation_coverage(m, captured, day, a!, b!, null)[0]).toBe('partial');
    expect(observation_coverage(m, captured, day, null, end, null)[0]).toBe('unknown');
    expect(observation_coverage(m, null, day, start, end, null)[0]).toBe('unknown');
    expect(observation_coverage(m, captured, day, start, end, 'Sample')[0]).toBe('partial');
    expect(
      observation_coverage(
        { ...m, timezone: 'Pacific/Auckland' },
        captured,
        day,
        start,
        end,
        null,
      )[0],
    ).toBe('unknown');
    expect(
      observation_coverage({ ...m, definition_revision: 2 }, captured, day, start, end, null)[0],
    ).toBe('complete');
    expect(observation_coverage(m, captured, '9999-12-31', start, end, null)[0]).toBe('unknown');
  });
  it('propagates coverage without simplifying numeric branches', () => {
    const count = definition('count', 'period_count', 'day'),
      reference = ref('count'),
      nested = assembled('nested', reference),
      outer: FormulaExpression = { op: 'reference_at', measure_id: 'nested', date: day };
    const observed = {
      ...observation('count', day, 0),
      query_start_at: 1,
      query_end_at: 2,
      limitation: 'Clipped',
      coverage_status: 'partial' as const,
      coverage_reasons: ['Clipped'],
    };
    const c = new Evaluation(definitions(count, nested), observations(observed), NOW);
    const direct = c.measure({ measure_id: 'count', date: day });
    expect([direct.value, direct.query_start_at, direct.limitation]).toEqual([0, 1, 'Clipped']);
    const result = c.expression(binary('multiply', outer, constant(0)), day, 'UTC');
    expect([result.value, result.status, result.coverage_status, result.coverage_reasons]).toEqual([
      0,
      'value',
      'partial',
      ['Clipped'],
    ]);
    expect(result.inputs.find((i) => i.measure_id === 'count')).toMatchObject({
      query_start_at: 1,
      query_end_at: 2,
      limitation: 'Clipped',
    });
    const branch: FormulaExpression = {
      op: 'if_before',
      date: '2026-10-28',
      before: constant(5),
      otherwise: outer,
    };
    expect(c.expression(branch, day, 'UTC').coverage_status).toBe('not_applicable');
    expect(c.expression(branch, '2026-10-29', 'UTC').coverage_status).toBe('partial');
    expect(
      combine_coverage([
        ['complete', []],
        ['unknown', ['Absent']],
        ['partial', ['Clipped']],
      ]),
    ).toEqual(['partial', ['Absent', 'Clipped']]);
    for (const formula of [constant(5), ref('snapshot')]) {
      const a = {
        ...count,
        id: 'assembled',
        origin: 'assembled' as const,
        source_id: null,
        formula,
      };
      const ctx = new Evaluation(
        definitions(a, definition('snapshot', 'running_total')),
        observations(observation('snapshot', day, -2.5)),
        NOW,
      );
      expect(ctx.measure({ measure_id: a.id, date: day }).coverage_status).toBe('unknown');
      expect(ctx.expression(formula, day, 'UTC').coverage_status).toBe('not_applicable');
    }
  });
  it.each([
    [true, null, null],
    [null, Infinity, null],
    [2, 2, null],
    [3, 2, null],
    [null, null, ' '],
  ])('rejects malformed coverage %s %s %s', (start, end, limitation) => {
    expect(() => validate_coverage(start, end, limitation)).toThrow();
  });
  it('arithmetic follows corrections, missing and zero', () => {
    const formula = binary(
      'multiply',
      binary('divide', binary('subtract', ref('taken'), ref('taken', -7)), ref('taken', -7)),
      constant(100),
    );
    const defs = definitions(definition('taken'), assembled('growth', formula)),
      pair = { measure_id: 'growth', date: day };
    const rows = observations(
      observation('taken', '2026-10-20', 100),
      observation('taken', day, 120),
    );
    expect(evaluate([pair], defs, rows, NOW)[0]!.value).toBe(20);
    rows.set(pair_key({ measure_id: 'taken', date: day }), observation('taken', day, 130));
    expect(evaluate([pair], defs, rows, NOW)[0]!.value).toBe(30);
    rows.set(
      pair_key({ measure_id: 'taken', date: '2026-10-20' }),
      observation('taken', '2026-10-20', 0),
    );
    expect(evaluate([pair], defs, rows, NOW)[0]!.status).toBe('division_by_zero');
    rows.delete(pair_key({ measure_id: 'taken', date: '2026-10-20' }));
    expect(evaluate([pair], defs, rows, NOW)[0]!.missing_inputs).toEqual([
      { measure_id: 'taken', date: '2026-10-20' },
    ]);
  });
  it('explicit windows ignore descriptive kinds and units', () => {
    const daily = definition('day', 'running_total'),
      weekly = {
        ...assembled('week', { op: 'sum_daily', measure_id: 'day', period: 'week' }),
        kind: 'period_count' as const,
        unit: 'different label',
        timezone: 'UTC',
      };
    const defs = definitions(daily, weekly);
    validate_graph('week', defs);
    const rows = observations();
    for (const [date, value] of [
      ['2026-10-26', 1],
      ['2026-10-27', 2],
      ['2026-10-28', 3],
      ['2026-10-29', 4],
      ['2026-10-30', 5],
      ['2026-10-31', 6],
      ['2026-11-01', 7],
    ] as const)
      rows.set(pair_key({ measure_id: 'day', date }), observation('day', date, value));
    expect(evaluate([{ measure_id: 'week', date: day }], defs, rows, NOW)[0]!.value).toBe(28);
    rows.delete(pair_key({ measure_id: 'day', date: '2026-11-01' }));
    expect(evaluate([{ measure_id: 'week', date: day }], defs, rows, NOW)[0]!.status).toBe(
      'missing_input',
    );
    // Native addition loses the small contribution between canceling large values.
    const values = [1e16, 1, -1e16, 0, 0, 0, 0];
    const dates = expression_references(weekly.formula!, day, 'UTC', NOW);
    for (const [index, pair] of dates.entries())
      rows.set(pair_key(pair), observation(pair.measure_id, pair.date, values[index]!));
    expect(evaluate([{ measure_id: 'week', date: day }], defs, rows, NOW)[0]!.value).toBe(0);
  });
  it('graph integrity does not police numeric kinds', () => {
    const a = assembled('a', ref('b', -1)),
      b = assembled('b', ref('a', -1));
    expect(() => validate_graph('a', definitions(a, b))).toThrow('cycle');
    validate_graph(
      'a',
      definitions({ ...a, kind: 'period_count', period: 'week' }, definition('b')),
    );
    validate_graph(
      'a',
      definitions(
        { ...a, kind: 'running_total', formula: binary('subtract', ref('b'), constant(1)) },
        definition('b', 'running_total'),
      ),
    );
    expect(parse_formula({ op: 'reference', measure_id: 'b', day_offset: 1000 }).op).toBe(
      'reference',
    );
    expect(() => parse_formula({ op: 'constant', value: true })).toThrow('finite');
  });
  it('retained totals are range independent and deep chains return a status', () => {
    const defs = definitions(
      definition('total', 'running_total'),
      assembled('combined', binary('multiply', ref('total'), constant(2))),
    );
    const rows = observations(
        observation('total', '2026-10-25', 10),
        observation('total', '2026-10-26', 13),
      ),
      one = { measure_id: 'combined', date: '2026-10-26' };
    expect(evaluate([one], defs, rows, NOW)[0]).toEqual(
      evaluate([{ measure_id: 'combined', date: '2026-10-25' }, one], defs, rows, NOW)[1],
    );
    const chain = definitions(definition('0'));
    for (let i = 1; i < 64; i++) {
      let formula = ref(String(i - 1));
      for (let j = 0; j < 15; j++) formula = binary('add', formula, constant(0));
      chain.set(String(i), assembled(String(i), formula));
    }
    validate_graph('63', chain);
    expect(demanded_pairs([{ measure_id: '63', date: day }], chain, NOW).size).toBe(64);
    expect(['arithmetic_limit', 'missing_input']).toContain(
      evaluate([{ measure_id: '63', date: day }], chain, new Map(), NOW)[0]!.status,
    );
  });
  it('dated paths match straight, compound and piecewise examples', () => {
    const elapsed: FormulaExpression = { op: 'elapsed_days', start: '2026-10-01' };
    const straight = binary('add', constant(100), binary('multiply', constant(2), elapsed)),
      compound = binary(
        'multiply',
        constant(100),
        binary('power', constant(1.1), binary('divide', elapsed, constant(7))),
      );
    const piecewise: FormulaExpression = {
      op: 'if_before',
      date: '2026-10-15',
      before: straight,
      otherwise: {
        op: 'if_before',
        date: '2026-10-22',
        before: constant(128),
        otherwise: binary(
          'multiply',
          constant(128),
          binary(
            'power',
            constant(1.1),
            binary('divide', binary('subtract', elapsed, constant(21)), constant(7)),
          ),
        ),
      },
    };
    for (const [date, expected] of [
      ['2026-10-01', [100, 100, 100, 130, 100]],
      ['2026-10-08', [114, 110, 114, 130, 114]],
      ['2026-10-15', [128, 121, 128, 130, 128]],
      ['2026-10-22', [142, 133.1, 128, 142, 142]],
      ['2026-10-29', [156, 146.41, 140.8, 156, 150]],
      ['2026-10-31', [160, 150.451743, 144.686875, 160, 150]],
    ] as const) {
      [
        straight,
        compound,
        piecewise,
        binary('max', constant(130), straight),
        binary('min', constant(150), straight),
      ].forEach((f, i) => {
        const m = assembled('path', parse_formula(f)),
          c = new Evaluation(definitions(m), new Map(), NOW);
        expect(c.expression(f, date, 'UTC').value).toBeCloseTo(expected[i]!, 5);
        expect(c.measure({ measure_id: 'path', date }).value).toBe(
          c.expression(f, date, 'UTC').value,
        );
      });
    }
  });
  it('date branches have static dependencies and lazy demand', () => {
    const absent: FormulaExpression = {
        op: 'reference_at',
        measure_id: 'deleted',
        date: '2026-10-01',
      },
      zero = binary('divide', constant(1), constant(0)),
      f: FormulaExpression = {
        op: 'if_before',
        date: '2026-10-15',
        before: constant(100),
        otherwise: { op: 'if_before', date: '2026-10-22', before: absent, otherwise: zero },
      };
    expect([...dependencies(f)]).toEqual(['deleted']);
    const m = assembled('path', f);
    expect(() => validate_graph('path', definitions(m))).toThrow('input measure');
    const c = new Evaluation(definitions(m), new Map(), NOW);
    expect(c.expression(f, '2026-10-14', 'UTC')).toMatchObject({
      value: 100,
      inputs: [],
      missing_inputs: [],
    });
    expect(expression_references(f, '2026-10-14', 'UTC', NOW)).toEqual([]);
    expect(c.expression(f, '2026-10-15', 'UTC')).toMatchObject({
      status: 'missing_input',
      missing_inputs: [{ measure_id: 'deleted', date: '2026-10-01' }],
      inputs: [],
    });
    expect(
      demanded_pairs([{ measure_id: 'path', date: '2026-10-15' }], c.definitions, NOW, null, true)
        .size,
    ).toBe(2);
    expect(c.expression(f, '2026-10-22', 'UTC').status).toBe('division_by_zero');
    const dormant: FormulaExpression = {
      op: 'if_before',
      date: '2026-11-01',
      before: constant(0),
      otherwise: f,
    };
    expect(phase_changed(dormant, '2026-10-15')).toBe(false);
    expect(phase_changed(dormant, '2026-11-01')).toBe(true);
    for (const date of ['2026-10-15', '2026-10-22']) expect(phase_changed(f, date)).toBe(true);
    expect(phase_changed(f, '2026-10-23')).toBe(false);
    expect(phase_changed(f, '0001-01-01')).toBe(false);
  });
  it('signed calendar days cross DST and strict ISO dates reject malformed values', () => {
    const c = new Evaluation(new Map(), new Map(), NOW);
    const nearMidnight = Date.parse('2026-01-01T23:30:00Z') / 1000;
    expect(local_date(nearMidnight, 'Factory')).toBe('2026-01-01');
    const localZone = realpathSync('/usr/share/zoneinfo/localtime').slice(
      '/usr/share/zoneinfo/'.length,
    );
    expect(local_date(nearMidnight, 'localtime')).toBe(local_date(nearMidnight, localZone));
    for (const [first, last] of [
      ['2026-03-28', '2026-03-30'],
      ['2026-10-24', '2026-10-26'],
    ] as const)
      for (const zone of ['Europe/Berlin', 'Pacific/Auckland']) {
        expect(c.expression({ op: 'elapsed_days', start: first }, last, zone).value).toBe(2);
        expect(c.expression({ op: 'elapsed_days', start: last }, first, zone).value).toBe(-2);
      }
    for (const bad of ['20261001', '2026-2-01', '2026-02-29', '2026-10-01T00:00:00', null])
      for (const raw of [
        { op: 'elapsed_days', start: bad },
        { op: 'reference_at', measure_id: 'taken', date: bad },
        { op: 'if_before', date: bad, before: constant(1), otherwise: constant(2) },
      ])
        expect(() => parse_formula(raw)).toThrow('date');
    for (const [a, b] of [
      [-1, 0.5],
      [0, -1],
      [1e308, 2],
    ])
      expect(c.expression(binary('power', constant(a!), constant(b!)), day, 'UTC').status).toBe(
        'invalid_value',
      );
  });
  it('fixed references retain provenance and follow corrections', () => {
    const m = { ...definition('baseline'), timezone: 'Pacific/Auckland', definition_revision: 2 },
      date = '2026-10-01',
      f: FormulaExpression = { op: 'reference_at', measure_id: 'baseline', date },
      o = { ...observation('baseline', date, 100), run_id: null, manual_write_id: 'write-1' };
    const c = new Evaluation(definitions(m), observations(o), NOW);
    for (const at of ['2026-09-01', '2026-10-29', '2027-01-01']) {
      expect(c.expression(f, at, 'Europe/Berlin')).toMatchObject({
        value: 100,
        inputs: [{ date, definition_revision: 2, manual_write_id: 'write-1' }],
      });
      expect(expression_references(f, at, 'Europe/Berlin', NOW)).toEqual([
        { measure_id: 'baseline', date },
      ]);
    }
    expect(
      new Evaluation(
        definitions(m),
        observations({ ...o, value: 120, manual_write_id: 'write-2' }),
        NOW,
      ).expression(f, day, 'UTC'),
    ).toMatchObject({ value: 120, inputs: [{ manual_write_id: 'write-2' }] });
  });
  it('demand budgets count unique transitive pairs without ordinary read caps', () => {
    const defs = definitions(
        definition('daily'),
        assembled('weekly', { op: 'sum_daily', measure_id: 'daily', period: 'week' }),
      ),
      root = { measure_id: 'weekly', date: day };
    expect(demanded_pairs([root, root], defs, NOW, 8).size).toBe(8);
    expect(() => demanded_pairs([root], defs, NOW, 7)).toThrow('7 pairs; request a narrower range');
    expect(demanded_pairs([root], defs, NOW).size).toBe(8);
  });
  it('phase boundaries follow composed references and evaluation dates', () => {
    const f: FormulaExpression = {
        op: 'if_before',
        date: '2026-10-28',
        before: constant(100),
        otherwise: constant(120),
      },
      defs = definitions(assembled('phase', f), assembled('nested', ref('phase'))),
      reference = ref('nested'),
      ctx = new Evaluation(defs, new Map(), NOW);
    expect(ctx.expression(reference, day, 'UTC').value).toBe(100);
    expect(ctx.expression(reference, '2026-10-28', 'UTC').value).toBe(120);
    expect(phase_changed(reference, '2026-10-28', defs)).toBe(true);
    expect(phase_changed(reference, '2026-10-29', defs)).toBe(false);
    expect(phase_changed(ref('phase', -2), '2026-10-28', defs)).toBe(false);
    expect(phase_changed(ref('phase', -2), '2026-10-30', defs)).toBe(true);
    const fixed: FormulaExpression = {
      op: 'reference_at',
      measure_id: 'phase',
      date: '2026-10-28',
    };
    expect(phase_changed(fixed, '2026-10-28', defs)).toBe(false);
    expect(phase_changed(binary('add', fixed, reference), '2026-10-28', defs)).toBe(true);
    const monthly: FormulaExpression = { op: 'sum_daily', measure_id: 'phase', period: 'month' };
    expect(phase_changed(monthly, '2026-11-01', defs)).toBe(true);
    expect(phase_changed(monthly, '2026-12-01', defs)).toBe(false);
    expect(phase_changed(ref('absent'), '2026-10-28', defs)).toBe(false);
    expect(phase_changed(ref('phase', 999999999), '2026-10-28', defs)).toBe(false);
  });
  it('phase detection reuses shared assembled dependencies', () => {
    let m = assembled('phase', {
      op: 'if_before',
      date: '2026-10-28',
      before: constant(100),
      otherwise: constant(120),
    });
    const defs = definitions(m);
    for (let i = 0; i < 20; i++) {
      m = assembled(`double-${i}`, binary('add', ref(m.id), ref(m.id)));
      defs.set(m.id, m);
    }
    expect(phase_changed(ref(m.id), '2026-10-28', defs)).toBe(true);
  });
});
