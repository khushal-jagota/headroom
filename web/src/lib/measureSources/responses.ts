import type { ListPageFacts } from '../types';
import type { CredentialMetadata, SourceRunSummary, SourceStatus } from './wire';
export type SourceListResponse = { sources: SourceStatus[]; page: ListPageFacts };
export type SourceRunsResponse = { runs: SourceRunSummary[]; page: ListPageFacts };
export type CredentialListResponse = { credentials: CredentialMetadata[]; page: ListPageFacts };
