export type ReadDetail = 'summary' | 'full';
export interface ListPageRequest { limit: number; offset: number }
export interface ListPageFacts {
  match_count: number; return_count: number; limit: number; offset: number;
  omitted_before: number; omitted_after: number; complete: boolean; next_offset: number | null;
}
