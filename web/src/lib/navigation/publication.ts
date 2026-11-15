import type { Continuity, NavigationRecord, SnapshotResponse, ViewRecord, ViewWindow, ViewCounts } from './contracts';

export interface NavigationPublication {
  readonly publicationId: number;
  readonly admissionToken: string;
  readonly continuity: Continuity;
  readonly cursor: string | null;
  readonly mode: SnapshotResponse['mode'];
  readonly authority: SnapshotResponse['authority'];
  readonly records: readonly (NavigationRecord | ViewRecord)[];
  readonly view?: { readonly id: string; readonly scopeGeneration: number; readonly contentVersion: string; readonly orderingGeneration: string; readonly windows: readonly ViewWindow[]; readonly counts: ViewCounts; readonly reviewSummary?: import("./contracts").ReviewSummary; readonly reviewContext?: readonly import('./contracts').ReviewConversationContext[] };
}
