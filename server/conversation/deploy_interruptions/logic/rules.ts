import type { CaptureState } from '../contracts.ts';
export function combine_capture_states(first: CaptureState, second: CaptureState): CaptureState {
  return first === 'unavailable' || second === 'unavailable' ? 'unavailable' : 'captured';
}
export function matches_occurrence(
  captured_occurrence: string | null,
  current_occurrence: string | null,
  captured_epoch: string | null,
  current_epoch: string | null,
  current_status: string | null,
): boolean {
  const exact =
    captured_occurrence !== null
      ? captured_occurrence === current_occurrence
      : captured_epoch !== null && captured_epoch === current_epoch;
  return exact && ['intent', 'running', 'unavailable'].includes(current_status ?? '');
}
export function claim_matches(values: {
  readonly exact: boolean;
  readonly captured_conversation: string;
  readonly current_conversation: string | null;
  readonly captured_revision: number | null;
  readonly current_revision: number;
  readonly captured_claim: string | null;
  readonly current_claim: string;
  readonly stage: string;
}): boolean {
  return (
    values.exact &&
    values.stage !== 'done' &&
    values.current_conversation === values.captured_conversation &&
    values.captured_claim === 'out' &&
    values.current_claim === 'out' &&
    values.captured_revision !== null &&
    values.captured_revision === values.current_revision
  );
}
