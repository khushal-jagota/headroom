import { describe, expect, it } from 'vitest';
import { PlannerFetchError } from '../src/lib/api';
import type { ManualMeasureState, Observation } from '../src/lib/goals/contracts';
import { manualAcceptedValue, manualConflictState, manualStateCanReplace, parseManualDraft } from '../src/lib/goals/manualMeasurements';

const observation = (date: string, value: number): Observation => ({
  measure_id: 'manual', date, value, run_id: null, manual_write_id: 'accepted-request',
  observed_at: 0, definition_revision: 1, period_interval: null, query_start_at: null, query_end_at: null, limitation: null, coverage_status: 'not_applicable', coverage_reasons: [],
});
const state: ManualMeasureState = {
  measure_id: 'manual', definition_revision: 1, write_revision: 4, timezone: 'Europe/Berlin',
  current_date: '2026-10-28', date: '2026-10-28', observation: null,
  latest_observation: observation('2026-10-27', 8.5),
};

describe('manual measurement editing boundary', () => {
  it('distinguishes an invalid blank from zero without prototype numeric restrictions', () => {
    expect(['', ' ', 'Infinity', 'NaN', '1e309', '0x10', '4 km'].map(parseManualDraft)).toEqual(Array(7).fill(null));
    expect(['0', '-2.75', '.125', '1.25e-7', '1000001'].map(parseManualDraft)).toEqual([0, -2.75, .125, 1.25e-7, 1000001]);
  });

  it('uses the selected observation or latest snapshot without summing or converting an absent date to zero', () => {
    expect(manualAcceptedValue(state, false)).toBeUndefined();
    expect(manualAcceptedValue(state, true)).toBe(8.5);
    const corrected = { ...state, observation: observation(state.date, 0), latest_observation: observation(state.date, 0) };
    expect(manualAcceptedValue(corrected, false)).toBe(0);
    expect(manualAcceptedValue(corrected, true)).toBe(0);
  });

  it('rejects stale revisions and a response for another selected date', () => {
    expect(manualStateCanReplace(state, { ...state, write_revision: 3 }, state.date)).toBe(false);
    expect(manualStateCanReplace(state, { ...state, definition_revision: 0 }, state.date)).toBe(false);
    expect(manualStateCanReplace(state, { ...state, date: '2026-10-27' }, state.date)).toBe(false);
    expect(manualStateCanReplace(state, { ...state, write_revision: 5 }, state.date)).toBe(true);
  });

  it('takes conflict state only from the typed HTTP conflict boundary', () => {
    expect(manualConflictState(new PlannerFetchError('Changed', 'manual_conflict', { status: 409, detail: { state } }))).toBe(state);
    expect(manualConflictState(new PlannerFetchError('Failed', 'http_error', { status: 500, detail: { state } }))).toBeUndefined();
    expect(manualConflictState({ status: 409, detail: { state } })).toBeUndefined();
    expect(manualConflictState(new PlannerFetchError('Changed', 'manual_conflict', { status: 409, detail: null }))).toBeUndefined();
  });
});

import {createManualMeasurementExamples} from '../src/lib/dev/manualMeasurementExamples';
import {chartData} from '../src/lib/charts/presentation';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ManualMeasurement} from '../src/lib/charts/components/manual-measurements';
it('keeps accepted manual daily samples unknown while ordinary snapshots retain comparisons',async()=>{
 const examples=createManualMeasurementExamples();
 for(const {block} of examples.definitions){
  const markup=renderToStaticMarkup(createElement(ManualMeasurement,{block,values:examples.values,adapter:examples.adapters.get(block.measure_id!),preferenceKey:`initial-${block.id}`}));
  expect(markup).toContain(`data-manual-model="${block.manual!.model}"`);
  expect(markup).toContain('No reading');
 }
 const daily=examples.definitions[0].block,reading=examples.definitions[2].block;
 const adapter=examples.adapters.get(daily.measure_id!)!;
 const next=await adapter.write({request_id:'manual-coverage',date:'2026-10-28',expected_definition_revision:1,expected_write_revision:0,basis:'date',operation:'replace',value:30});
 expect(next.state.observation).toMatchObject({value:30,query_start_at:null,query_end_at:null,limitation:null,coverage_status:'unknown'});
 expect(chartData(daily,examples.values,'all').at(-1)?.comparisons?.actual).toBeNull();
 expect(chartData(reading,examples.values,'all').at(-1)?.coverage_status).toBe('not_applicable');
});
