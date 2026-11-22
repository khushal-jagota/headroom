/** Inputs to the permanent copy of an already checked Actions publication. */
export interface GitHubPublicationOptions {
  readonly repository: string;
  readonly source_sha: string;
  readonly publication_directory: string;
  readonly token: string;
  readonly signal: AbortSignal;
  readonly fetch?: typeof fetch;
}
