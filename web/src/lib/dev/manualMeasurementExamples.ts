import { PlannerFetchError } from '../api';
import type { ChartBlock, ManualMeasureState, ManualWriteBody, ManualWriteResult, MeasureValue, Observation } from '../goals/contracts';
import type { ManualMeasurementAdapter } from '../goals/manualMeasurements';

// These adapters are local sample state. They do not call any measurement API.
export const manualSampleDate = '2026-10-28';
const dates = ['2026-10-22', '2026-10-23', '2026-10-24', '2026-10-25', '2026-10-26', '2026-10-27', manualSampleDate];

export function createManualMeasurementExamples() {
  const definitions: Array<{ label: string; block: ChartBlock; numbers: Array<number | undefined> }> = [
    { label: 'Daily quota', block: { type: 'chart', id: 'manual-daily', placement: 'primary', kind: 'bar', title: 'Messages sent', measure_id: 'sample-manual-messages', target: 20, manual: { model: 'daily_quota', increments: [1, 5] } }, numbers: [22, 20, 16, undefined, 14, 17, 12] },
    { label: 'Reach a target', block: { type: 'chart', id: 'manual-total', placement: 'primary', kind: 'gauge', title: 'Customer interviews', measure_id: 'sample-manual-interviews', target: 12, manual: { model: 'cumulative_target', increments: [1, 5] } }, numbers: [undefined, undefined, undefined, undefined, undefined, undefined, 8] },
    { label: 'Record a reading', block: { type: 'chart', id: 'manual-reading', placement: 'primary', kind: 'line', title: 'Run distance', measure_id: 'sample-manual-runs', target: 10, unit: 'km', series: [{ name: 'Run', points: [] }], format: { decimals: 1 }, manual: { model: 'individual_readings' } }, numbers: [3.2, 4.1, 3.8, 5.2, 5.0, 5.8, 6.4] },
  ];
  const adapters = new Map<string, ManualMeasurementAdapter>();
  const values: MeasureValue[] = [];
  for (const { block, numbers } of definitions) {
    const measureId = block.measure_id!;
    let revision = 0;
    const records = new Map<string, Observation>();
    const receipts = new Map<string, { fingerprint: string; result: ManualWriteResult }>();
    numbers.forEach((value, index) => {
      values.push({ measure_id: measureId, date: dates[index], definition_revision: 1, status: value === undefined ? 'missing_input' : 'value', value: value ?? null, inputs: [], missing_inputs: [] });
      if (value !== undefined) records.set(dates[index], { measure_id: measureId, date: dates[index], value, run_id: null, manual_write_id: `sample-${measureId}-${index}`, observed_at: 0, definition_revision: 1, period_interval: null });
    });
    const state = (date = manualSampleDate): ManualMeasureState => ({ measure_id: measureId, definition_revision: 1, write_revision: revision, timezone: 'Europe/Berlin', current_date: manualSampleDate, date,
      observation: records.get(date) ?? null, latest_observation: [...records.values()].filter(value => value.date <= date).sort((a, b) => b.date.localeCompare(a.date))[0] ?? null });
    adapters.set(measureId, {
      read: async date => state(date),
      async write(body: ManualWriteBody) {
        const fingerprint = JSON.stringify(body), previous = receipts.get(body.request_id);
        if (previous) {
          if (previous.fingerprint !== fingerprint) throw new PlannerFetchError('This request changed.', 'manual_request_conflict', { status: 409, detail: { state: state(body.date) } });
          return { ...previous.result, state: state(body.date), replayed: true };
        }
        if (body.operation === 'replace' && body.expected_write_revision !== revision) throw new PlannerFetchError('This reading changed.', 'manual_write_conflict', { status: 409, detail: { state: state(body.date) } });
        const basis = state(body.date), base = (body.basis === 'latest' ? basis.latest_observation : basis.observation)?.value ?? 0;
        const value = body.operation === 'replace' ? body.value : base + body.delta;
        if (!Number.isFinite(value)) throw new PlannerFetchError('Enter a finite number.', 'invalid_value');
        revision++;
        records.set(body.date, { measure_id: measureId, date: body.date, value, run_id: null, manual_write_id: body.request_id, observed_at: Date.now() / 1000, definition_revision: 1, period_interval: null });
        const result: ManualWriteResult = { receipt: { request_id: body.request_id, measure_id: measureId, actor: { kind: 'owner', id: 'sample-owner' }, payload_fingerprint: fingerprint, date: body.date, value, write_revision: revision, accepted_at: Date.now() / 1000 }, state: state(body.date), replayed: false };
        receipts.set(body.request_id, { fingerprint, result });
        return result;
      },
    });
  }
  return { definitions, values, adapters };
}
