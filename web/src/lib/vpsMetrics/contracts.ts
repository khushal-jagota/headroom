// Wire types mirror planner/environments/vps_metrics/contracts.py.
export type Availability = 'available' | 'partial' | 'unavailable';
export type Resolution = 'raw' | 'minute' | 'coarse';
export interface Measurement { value: number | null; unit: string; reason: string | null; sampled_at?: number; numerator?: number | null; duration?: number | null }
export interface ProcessRecord { identity: { boot_id: string; pid: number; start_ticks: number }; parent_pid: number; uid: number | null; name: string; state: string; service: string; metrics: Record<string, Measurement> }
export interface ServiceRecord { identity: string; name: string; kind: string; metrics: Record<string, Measurement>; state: string | null; state_reason: string | null; process_count: number }
export interface Sample { timestamp: number; monotonic: number; boot_id: string; expected_interval: number; core_count: number; metrics: Record<string, Measurement>; services: ServiceRecord[]; processes: ProcessRecord[]; process_count: number; process_availability: Availability; process_reason: string | null; disk_total_members: string[]; network_total_members: string[] }
export interface RetentionCoverage { requested_start: number | null; retained_start: number | null; retained_end: number | null; budget_evicted: boolean; policy_seconds: number }
export interface CollectionStatus { availability: Availability; reason: string | null; last_sample_at: number | null; last_attempt_at: number | null; cadence_seconds: number }
export interface LatestResponse { sequence: number | null; sample: Sample | null; collection: CollectionStatus; coverage: RetentionCoverage }
export interface ProcessPage { sequence: number | null; timestamp: number | null; processes: ProcessRecord[]; total: number; offset: number; next_offset: number | null; availability: Availability; reason: string | null; coverage: RetentionCoverage }
export interface AggregateStatistics { weighted_sum: number; valid_duration: number; expected_duration: number; minimum: number | null; maximum: number | null; count: number; numerator: number | null; denominator: number | null }
export interface HistoryPoint { timestamp: number; end: number; boot_id: string; value: number | null; unit: string; reason: string | null; statistics: AggregateStatistics | null }
export interface HistoryResponse { signal: string; entity: string | null; resolution: Resolution; points: HistoryPoint[]; coverage: RetentionCoverage }
export type WindowId = '5m' | '24h' | '30d';
export type Severity = 'normal' | 'raised' | 'wrong';
export type InstrumentId = 'cpu' | 'memory' | 'load' | 'disk' | 'io' | 'network';
export interface Reading { value: number | null; reason: string | null; unit: string }
export interface Instrument { id: InstrumentId; title: string; primary: Reading; secondary: Reading | null; waiting?: Reading | null; ceiling?: number | null; details: string[]; severity: Severity; pill: string | null }
export interface WorkloadRow { id: string; name: string; count: number; cpu: Reading; memory: Reading; waiting: Reading | null; share: number | null; partial: boolean; state: string | null }
export interface HistoryRange { start: number; end: number; resolution: Resolution }
export interface TrendPoint { timestamp: number; end: number; value: number | null; boot_id: string }
export interface Trend { primary: TrendPoint[]; secondary: TrendPoint[]; start: number; end: number; gapSeconds: number }
export interface VpsMetricsState { latest: LatestResponse | null; processes: ProcessPage | null; window: WindowId; history: Partial<Record<InstrumentId, Trend>>; loading: boolean; historyLoading: boolean; failed: boolean; now: number; processError: string | null; historyError: string | null }
export interface MetricsApi { latest(signal: AbortSignal): Promise<LatestResponse>; activity(signal: AbortSignal): Promise<{ active_until: number }>; processes(sequence: number | null, offset: number, signal: AbortSignal): Promise<ProcessPage>; history(key: string, range: HistoryRange, signal: AbortSignal): Promise<HistoryResponse> }
export interface MetricsController { subscribe(listener: (state: VpsMetricsState) => void): () => void; setVisible(visible: boolean): void; setWindow(window: WindowId): void; destroy(): void }
