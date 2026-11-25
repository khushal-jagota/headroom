/** The one place the server names where releases come from: a GitHub repository and its publishing workflow. */
import { readFileSync } from 'node:fs';

export const RELEASE_REPOSITORY = 'khushal-jagota/headroom';
export const TRUSTED_RELEASE_WORKFLOW = '.github/workflows/publish-typescript-release.yml';

/** Public release assets support anonymous reads. An explicitly configured credential must work. */
export function release_request_headers(
  token_file: string | null,
  accept = 'application/vnd.github+json',
): Record<string, string> {
  if (token_file === null) return { Accept: accept };
  const token = readFileSync(token_file, 'utf8').trim();
  if (!token) throw new Error('The repository update credential is unavailable.');
  return { Accept: accept, Authorization: `Bearer ${token}` };
}
