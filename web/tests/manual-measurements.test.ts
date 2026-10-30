import { describe, expect, it, vi } from 'vitest';
import { PlannerFetchError } from '../src/lib/api';
import type { ManualMeasureState, ManualWriteBody, ManualWriteResult, Observation } from '../src/lib/goals/contracts';
import { manualAcceptedValue, manualConflictState, manualStateCanReplace, parseManualDraft, submitManualWrite } from '../src/lib/goals/manualMeasurements';
import { createGoalRequestSessions, type ManualWriteCommand } from '../src/lib/goals/requestSessions';

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
  it('retains cumulative drafts during preflight and freezes the submitted body before HTTP retries', async () => {
    const sessions = createGoalRequestSessions(), session = sessions.manual('goal', 'manual');
    let rejectRead!: (error: Error) => void;
    const preflight = new Promise<ManualMeasureState>((_resolve, reject) => { rejectRead = reject; });
    const read = vi.fn().mockReturnValueOnce(preflight).mockResolvedValue({ ...state, current_date: '2026-10-29' });
    const receipt: ManualWriteResult = { state, replayed: false, receipt: { request_id: 'original', measure_id: 'manual', actor: { kind: 'owner', id: 'owner' }, payload_fingerprint: 'fingerprint', date: '2026-10-29', value: 19, write_revision: 5, accepted_at: 0 } };
    const write = vi.fn<(body: ManualWriteBody) => Promise<ManualWriteResult>>().mockRejectedValueOnce(new Error('Write rejected')).mockResolvedValue(receipt);
    const adapter = { read, write };
    const intent: ManualWriteCommand = { request_id: 'original', operation: 'replace', basis: 'latest', preflight: true, date: state.date, value: 19, expected_definition_revision: 1, expected_write_revision: 4 };
    const pending = submitManualWrite(session, intent, adapter);
    const reopened = sessions.manual('goal', 'manual');
    expect(reopened.current).toEqual({ status: 'pending', command: intent });
    expect(write).not.toHaveBeenCalled();
    const failed = expect(pending).rejects.toThrow('Read rejected');
    rejectRead(new Error('Read rejected')); await failed;
    expect(reopened.current).toMatchObject({ status: 'failed', command: intent });
    expect(read).toHaveBeenCalledTimes(1); expect(write).not.toHaveBeenCalled();
    await expect(submitManualWrite(reopened, reopened.current!.command, adapter)).rejects.toThrow('Write rejected');
    const body = write.mock.calls[0][0];
    expect(body).toEqual({ request_id: 'original', operation: 'replace', basis: 'latest', date: '2026-10-29', value: 19, expected_definition_revision: 1, expected_write_revision: 4 });
    expect(reopened.current?.command).toBe(body);
    await submitManualWrite(reopened, reopened.current!.command, adapter);
    expect(read).toHaveBeenCalledTimes(2);
    expect(write.mock.calls[1][0]).toBe(body);
    expect(reopened.current).toBeUndefined();
  });

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
it('compares manual daily records in both target directions while preserving unknown coverage and missing days', async () => {
 const examples = createManualMeasurementExamples();
 const daily = examples.definitions[0].block, reading = examples.definitions[2].block;
 const render = (block = daily, values = examples.values) => renderToStaticMarkup(createElement(ManualMeasurement, {
  block, values, adapter: examples.adapters.get(block.measure_id!), preferenceKey: `initial-${block.id}`,
 }));
 const bars = (markup: string) => [...markup.matchAll(/<button[^>]*class="bar-pick"[^>]*>[\s\S]*?<\/button>/g)].map(match => match[0]);
 const dailyBars = bars(render());
 expect(dailyBars).toHaveLength(7);
 expect(dailyBars[0]).toContain('background-color:var(--accent-done)');
 expect(dailyBars[1]).toContain('background-color:var(--accent-done)');
 expect(dailyBars[2]).toContain('background-color:var(--accent-error)');
 expect(dailyBars[3]).toContain('class="bar missing"');
 expect(dailyBars.at(-1)).toContain('background-color:var(--accent-error)');
 const lowerBars = bars(render({ ...daily, direction: 'atmost' }));
 expect(lowerBars[0]).toContain('background-color:var(--accent-error)');
 expect(lowerBars[1]).toContain('background-color:var(--accent-done)');
 expect(lowerBars[2]).toContain('background-color:var(--accent-done)');
 const corrected = examples.values.map(item => item.measure_id === daily.measure_id && item.date === '2026-10-28' ? { ...item, value: 0 } : item);
 const zeroBar = bars(render({ ...daily, direction: 'atmost' }, corrected)).at(-1)!;
 expect(zeroBar).toContain('28 Oct, 0 messages sent, target 20');
 expect(zeroBar).toContain('background-color:var(--accent-done)');
 expect(zeroBar).not.toContain('bar missing');
 const dated = bars(render({ ...daily, reference: [{ date: '2026-10-22', value: 30 }, { date: '2026-10-28', value: 10 }] }));
 expect(dated[0]).toContain('background-color:var(--accent-error)');
 expect(dated.at(-1)).toContain('background-color:var(--accent-done)');
 expect(render()).not.toContain('class="comparison"');
 expect(render()).not.toContain('selected-date');
 expect(render()).toContain('aria-label="Messages sent for 28 Oct"');
 const adapter = examples.adapters.get(daily.measure_id!)!;
 const next = await adapter.write({ request_id: 'manual-coverage', date: '2026-10-28', expected_definition_revision: 1, expected_write_revision: 0, basis: 'date', operation: 'replace', value: 30 });
 expect(next.state.observation).toMatchObject({ value: 30, query_start_at: null, query_end_at: null, limitation: null, coverage_status: 'unknown' });
 expect(chartData(daily, examples.values, 'all').at(-1)?.comparisons?.actual).toBe(false);
 expect(chartData(reading, examples.values, 'all').at(-1)?.coverage_status).toBe('not_applicable');
});
