import type {
  TranscriptDirectory,
  UsagePricing,
  UsageSettings,
  UsageSummary,
  UsageSummaryInput,
} from './contracts.ts';

export interface UsageWorkerData {
  readonly stateDir: string;
  readonly injectedRates: boolean;
  readonly injectedClock: boolean;
}
export interface UsageWorkerError {
  readonly message: string;
  readonly name: string;
  readonly reason?: string;
  readonly detail?: string;
}
export type UsageWorkerRequest =
  | {
      readonly kind: 'summary';
      readonly id: number;
      readonly input: UsageSummaryInput;
      readonly settings: UsageSettings;
      readonly directories: readonly TranscriptDirectory[];
    }
  | { readonly kind: 'refresh'; readonly id: number }
  | {
      readonly kind: 'callback-result';
      readonly id: number;
      readonly value?: unknown;
      readonly error?: UsageWorkerError;
    };
export type UsageWorkerResponse =
  | { readonly kind: 'result'; readonly id: number; readonly value: UsageSummary | UsagePricing }
  | { readonly kind: 'error'; readonly id: number; readonly error: UsageWorkerError }
  | { readonly kind: 'callback'; readonly id: number; readonly callback: 'rates' | 'clock' }
  | { readonly kind: 'cancel-callback'; readonly id: number };
