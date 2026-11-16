/** A stopped helper records evidence only. It never initializes an application database. */
import { join } from 'node:path';
import { createReadAccess } from '../../core/database/index.ts';
import {
  acquireRuntimeOwnership,
  canonical_database_path,
  ConversationRuntimeAlreadyOwnedError,
} from '../../core/locks/index.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { read_unresolved_turns } from '../../conversation/deploy_interruptions/capture.ts';
import { CAPTURE_VERSION } from '../../conversation/deploy_interruptions/contracts.ts';
import type {
  CaptureManifest,
  CapturedTurn,
} from '../../conversation/deploy_interruptions/contracts.ts';
import {
  manifest_from_dict,
  manifest_root,
  write_manifest,
} from '../../conversation/deploy_interruptions/files.ts';
import { expand_path } from '../paths.ts';

/** Diagnostic projection only. Keep native/core errors themselves unchanged. */
function failure_detail(error: unknown, canonical: string): string {
  if (error instanceof ConversationRuntimeAlreadyOwnedError)
    return `ConversationRuntimeAlreadyOwned: ${canonical}`;
  if (!(error instanceof Error)) return `Error: ${String(error)}`;
  return `${error.name}: ${error.message}`;
}

export function capture_stopped_database(
  database: string,
  deployment_id: string,
  capture_id: string,
): CaptureManifest {
  const admitted = assert_test_database_path(database);
  // files.manifest_root expands user notation; admit its actual spelling separately.
  assert_test_database_path(expand_path(database));
  const captured_at = Math.trunc(Date.now() / 1000);
  const unavailable = (detail: string): CaptureManifest => ({
    deployment_id,
    capture_id,
    captured_at,
    state: 'unavailable',
    detail,
    turns: [],
    version: CAPTURE_VERSION,
  });
  const initial = unavailable('The interruption capture did not complete.');
  manifest_from_dict(initial);
  assert_test_database_path(join(manifest_root(database), deployment_id, capture_id + '.json'));
  write_manifest(database, initial);
  let result: CaptureManifest,
    canonical = database;
  try {
    canonical = canonical_database_path(admitted);
    const lease = acquireRuntimeOwnership(admitted);
    try {
      const reader = createReadAccess(canonical, { busyTimeoutMs: 0 });
      let turns: readonly CapturedTurn[];
      try {
        turns = reader.read((sql) => read_unresolved_turns(sql));
      } finally {
        reader.close();
      }
      result = {
        deployment_id,
        capture_id,
        captured_at,
        state: 'captured',
        detail: null,
        turns,
        version: CAPTURE_VERSION,
      };
      write_manifest(database, result);
      return result;
    } finally {
      lease.close();
    }
  } catch (error) {
    result = unavailable(
      Array.from('Interruption capture failed: ' + failure_detail(error, canonical))
        .slice(0, 8192)
        .join(''),
    );
  }
  write_manifest(database, result);
  return result;
}
