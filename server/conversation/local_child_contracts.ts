/** Durable evidence identifies one local child, independently of its live handle. */
export interface LocalChildIdentity {
  readonly pid: number;
  readonly birth: string;
  readonly boot: string;
  readonly unit: string | null;
  readonly supervision_record: string | null;
}
export interface LocalHostBootIdentity {
  readonly host: string;
  readonly boot: string;
}
export type RecordedSourceSettlement = 'retired' | 'undelivered';
