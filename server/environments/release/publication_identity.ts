/** Actual GitHub intake shared by the build and final receipt entries. */
import { readFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { is_object } from '../updates/codec.ts';
import { validate_app_sha } from './logic/manifest.ts';
import { run_release_command } from './process.ts';
import { PUBLICATION_GIT_SECONDS } from './publication_configuration.ts';
import type { ReleaseExecution } from './contracts.ts';
import type { PublicationSourceRun } from './publication_contracts.ts';

export const TYPESCRIPT_PUBLICATION_WORKFLOW = '.github/workflows/publish-typescript-release.yml';

/** Source reads retain the caller's owner, signal, and original deadline. */
export async function publication_git(
  root: string,
  args: readonly string[],
  execution: ReleaseExecution,
): Promise<string> {
  return (
    await run_release_command(
      ['git', '-C', root, ...args],
      {
        timeout_seconds: PUBLICATION_GIT_SECONDS,
        environment: {
          PATH: dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin',
          LANG: 'C.UTF-8',
          GIT_CONFIG_NOSYSTEM: '1',
          GIT_CONFIG_SYSTEM: '/dev/null',
          GIT_CONFIG_GLOBAL: '/dev/null',
          GIT_NO_REPLACE_OBJECTS: '1',
        },
      },
      execution,
    )
  ).stdout;
}

export async function require_clean_publication_source(
  root: string,
  expected_sha: string,
  execution: ReleaseExecution,
): Promise<void> {
  validate_app_sha(expected_sha);
  if ((await publication_git(root, ['rev-parse', 'HEAD'], execution)).trim() !== expected_sha)
    throw new Error('Publication source HEAD differs from the event SHA');
  if (await publication_git(root, ['status', '--porcelain=v1', '--untracked-files=all'], execution))
    throw new Error('Publication source must have a clean working tree');
}
export async function publication_json_file(path: string): Promise<unknown> {
  return JSON.parse(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(path)),
  );
}
export function publication_text(value: unknown, name: string): string {
  if (typeof value !== 'string' || !value || value.includes('\0'))
    throw new Error(`Invalid publication ${name}`);
  return value;
}
export function publication_id(value: unknown, name: string): string {
  const text = publication_text(value, name);
  if (/^[1-9][0-9]*$/.exec(text)?.[0] !== text) throw new Error(`Invalid publication ${name}`);
  return text;
}
export async function source_run(requested_sha: string): Promise<PublicationSourceRun> {
  const env = process.env,
    get = (name: string) => publication_text(env[name], name);
  if (env.GITHUB_ACTIONS !== 'true')
    throw new Error('Publication build requires actual GitHub run provenance');
  const repository = get('GITHUB_REPOSITORY'),
    sha = validate_app_sha(get('GITHUB_SHA')),
    event = get('GITHUB_EVENT_NAME'),
    ref = get('GITHUB_REF'),
    workflow_ref = get('GITHUB_WORKFLOW_REF');
  const payload = await publication_json_file(get('GITHUB_EVENT_PATH'));
  if (
    !is_object(payload) ||
    !is_object(payload.repository) ||
    payload.repository.full_name !== repository ||
    sha !== requested_sha
  )
    throw new Error('Publication GitHub event does not match its source');
  if (
    (event === 'push' && (payload.after !== sha || payload.ref !== ref)) ||
    (typeof payload.ref === 'string' && payload.ref !== ref)
  )
    throw new Error('Publication GitHub event does not match its ref or SHA');
  if (
    event === 'pull_request' &&
    (!is_object(payload.pull_request) || payload.pull_request.merge_commit_sha !== sha)
  )
    throw new Error('Publication pull request does not match the selected merge SHA');
  const prefix = repository + '/',
    separator = workflow_ref.lastIndexOf('@'),
    workflow_path = workflow_ref.slice(prefix.length, separator);
  if (
    !workflow_ref.startsWith(prefix) ||
    separator < prefix.length ||
    !/^\.github\/workflows\/[^/]+\.ya?ml$/.test(workflow_path) ||
    !ref.startsWith('refs/')
  )
    throw new Error('Invalid publication workflow identity');
  return {
    repository,
    source_sha: sha,
    event,
    ref,
    workflow_path,
    workflow_ref,
    run_id: publication_id(env.GITHUB_RUN_ID, 'GITHUB_RUN_ID'),
    run_attempt: publication_id(env.GITHUB_RUN_ATTEMPT, 'GITHUB_RUN_ATTEMPT'),
  };
}
export function require_publication_run(run: PublicationSourceRun): void {
  validate_app_sha(run.source_sha);
  publication_id(run.run_id, 'run ID');
  publication_id(run.run_attempt, 'run attempt');
  if (
    run.event !== 'push' ||
    run.ref !== 'refs/heads/main' ||
    run.workflow_path !== TYPESCRIPT_PUBLICATION_WORKFLOW ||
    run.workflow_ref !== `${run.repository}/${TYPESCRIPT_PUBLICATION_WORKFLOW}@refs/heads/main`
  )
    throw new Error('Receipt requires the reserved main-push publication workflow');
}
