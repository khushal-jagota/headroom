import { fetchJson, PlannerFetchError } from '../api';
import { queryClient } from '../queryClient';
import type { ManualMeasureState, ManualWriteBody, ManualWriteResult } from './contracts';
import type { ManualWriteCommand, RequestSession } from './requestSessions';

/** The production editor and the explicitly local development samples share this boundary. */
export interface ManualMeasurementAdapter {
  read(date?: string): Promise<ManualMeasureState>;
  write(body: ManualWriteBody): Promise<ManualWriteResult>;
}

/** Own the intent before the cumulative date read, then retain the exact HTTP body. */
export function submitManualWrite(session: RequestSession<ManualWriteCommand>, command: ManualWriteCommand, adapter: ManualMeasurementAdapter): Promise<ManualWriteResult> {
  return session.run(command, async prepared => {
    let body: ManualWriteBody = command;
    if (command.preflight) {
      const current = await adapter.read();
      const { preflight: _preflight, ...intent } = command;
      body = { ...intent, date: current.current_date };
      prepared(body);
    }
    return adapter.write(body);
  });
}

export function manualMeasurementHttp(measureId: string): ManualMeasurementAdapter {
  const path = `/api/measures/${encodeURIComponent(measureId)}`;
  return {
    read: date => fetchJson<ManualMeasureState>(`${path}/manual-state${date ? `?date=${encodeURIComponent(date)}` : ''}`),
    async write(body) {
      const result = await fetchJson<ManualWriteResult>(`${path}/manual-writes`, { method: 'POST', body });
      // A failed refresh cannot turn an accepted write into a failed write.
      void queryClient.invalidateQueries().catch(() => {});
      return result;
    },
  };
}

export function parseManualDraft(text: string): number | null {
  if (!text.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text.trim())) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function manualAcceptedValue(state: ManualMeasureState, cumulative: boolean): number | undefined {
  return (cumulative ? state.latest_observation : state.observation)?.value;
}

export function isManualWriteConflict(error: unknown): error is PlannerFetchError {
  return error instanceof PlannerFetchError && error.status === 409;
}

export function manualErrorMessage(error: unknown): string {
  if (isManualWriteConflict(error)) return 'This reading changed. Press Escape to use the accepted value, or edit again.';
  return error instanceof Error ? error.message : 'Could not save this reading.';
}

export function manualConflictState(error: unknown): ManualMeasureState | undefined {
  if (!isManualWriteConflict(error) || !error.detail || typeof error.detail !== 'object') return undefined;
  const detail = error.detail as { state?: ManualMeasureState; current_state?: ManualMeasureState };
  return detail.state ?? detail.current_state;
}


/** Read responses cannot regress an accepted revision or replace another selected date. */
export function manualStateCanReplace(current: ManualMeasureState | null, next: ManualMeasureState, date?: string): boolean {
  return (date === undefined || next.date === date) && (!current || next.measure_id === current.measure_id && next.definition_revision >= current.definition_revision && next.write_revision >= current.write_revision);
}
