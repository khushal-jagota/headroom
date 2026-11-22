/** Only the workflow's final checked-publication step calls this entry. */
import { publish_github_release } from './github_publication.ts';
import { publication_text } from './publication_identity.ts';

try {
  if (process.argv.length !== 3) throw new Error('Expected one publication directory');
  console.log(
    await publish_github_release({
      publication_directory: process.argv[2]!,
      repository: publication_text(process.env.GITHUB_REPOSITORY, 'GITHUB_REPOSITORY'),
      source_sha: publication_text(process.env.GITHUB_SHA, 'GITHUB_SHA'),
      token: publication_text(process.env.GITHUB_TOKEN, 'GITHUB_TOKEN'),
      signal: AbortSignal.timeout(30 * 60 * 1000),
    }),
  );
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
