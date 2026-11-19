/** Claude's card: its recipe, its `auth status` answer and its local model catalog. */
import { readFileSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { is_record as object } from '../../../core/records.ts';
import { probe_card } from '../../../conversation/snapshot/probe.ts';
import type { ClaudeModelCatalogSource } from './contracts.ts';
import type { DriverStatus } from '../../core/status.ts';
import { claude_driver } from '../driver.ts';
import { claude_manifest } from '../manifest.ts';
import { claude_models } from '../models.ts';
import { claude_catalog_reader, read_claude_identity } from './status_readers.ts';

export interface ClaudeAuthPaths {
  readonly credentials_path: string;
  readonly claude_json_path: string;
}

const OAUTH_ACCOUNT_FIELDS = [
  'accountUuid',
  'emailAddress',
  'organizationUuid',
  'organizationName',
  'organizationType',
  'organizationRole',
  'workspaceRole',
  'billingType',
  'seatTier',
] as const;

function is_missing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}

function read_file_metadata(
  path: string,
): { readonly mtime_ms: number; readonly size: number } | 'missing' | 'unreadable' {
  try {
    const info = statSync(path);
    return { mtime_ms: info.mtimeMs, size: info.size };
  } catch (error) {
    return is_missing(error) ? 'missing' : 'unreadable';
  }
}

function read_oauth_account_metadata(
  path: string,
): Record<string, unknown> | null | 'missing' | 'unreadable' {
  let document: unknown;
  try {
    document = JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    return is_missing(error) ? 'missing' : 'unreadable';
  }
  if (!object(document) || !object(document.oauthAccount)) return null;
  const account: Record<string, unknown> = {};
  for (const field of OAUTH_ACCOUNT_FIELDS) {
    const value = document.oauthAccount[field];
    if (value !== undefined) account[field] = value;
  }
  return account;
}

export function claude_auth_paths(
  claude_home: string,
  environment: Readonly<Record<string, string | undefined>>,
  account_home: string = homedir(),
): ClaudeAuthPaths {
  const configured_dir = environment[claude_manifest.home!.environment_name];
  return {
    credentials_path: join(claude_home, '.credentials.json'),
    claude_json_path: configured_dir
      ? join(claude_home, '.claude.json')
      : join(environment.HOME || account_home, '.claude.json'),
  };
}

/** Non-secret local auth metadata only; never reads `.credentials.json` contents or Keychain. */
export function read_claude_auth_freshness(paths: ClaudeAuthPaths): string {
  return JSON.stringify({
    credentials: read_file_metadata(paths.credentials_path),
    oauth_account: read_oauth_account_metadata(paths.claude_json_path),
  });
}

export function create_claude_status(sources: {
  /** Null: the card lists no models and says Claude did not answer. */
  readonly catalog: ClaudeModelCatalogSource | null;
  /** Null or omitted: the card does not track local Claude auth metadata. */
  readonly read_freshness?: (() => string | null) | null;
}): DriverStatus {
  const read_catalog = claude_catalog_reader(sources.catalog);
  return {
    version_detects_upgrade: true,
    ...(sources.read_freshness ? { freshness_key: () => sources.read_freshness?.() ?? null } : {}),
    probe: (context) =>
      probe_card(
        {
          driver_kind: claude_driver.driver_kind,
          manifest: claude_manifest,
          models: claude_models,
          read_identity: read_claude_identity,
          read_catalog,
        },
        context,
      ),
  };
}
