/** Bounded immutable capture files, read before the startup write transaction. */
import {
  closeSync,
  constants,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { resolve_path_sync } from '../../core/path_resolution.ts';
import { expand_path } from '../../environments/paths.ts';
import { randomUUID } from 'node:crypto';
import { CAPTURE_VERSION } from './contracts.ts';
import type { CaptureManifest, CapturedTurn } from './contracts.ts';
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const MAX_BYTES = 16 * 1024 * 1024;
function identity(value: unknown): string {
  if (typeof value !== 'string' || !SAFE_ID.test(value) || value === '.' || value === '..') {
    throw new Error('invalid capture identity');
  }
  return value;
}
function string(value: unknown, optional: true): string | null;
function string(value: unknown, optional?: false): string;
function string(value: unknown, optional = false): string | null {
  if ((value === null || value === undefined) && optional) return null;
  if (typeof value !== 'string' || !value || [...value].length > 8192) {
    throw new Error('invalid capture string');
  }
  return value;
}
function integer(value: unknown, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < minimum) {
    throw new Error('invalid capture integer');
  }
  return value;
}
function object(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
export function manifest_from_dict(value: unknown): CaptureManifest {
  const raw = object(value);
  if (!raw || raw.version !== CAPTURE_VERSION) throw new Error('unsupported capture manifest');
  const deployment_id = identity(raw.deployment_id);
  const capture_id = identity(raw.capture_id);
  const state = raw.state;
  if (state !== 'captured' && state !== 'unavailable') throw new Error('invalid capture state');
  if (!Array.isArray(raw.turns) || (state === 'unavailable' && raw.turns.length > 0)) {
    throw new Error('invalid captured turns');
  }
  const turns = raw.turns.map((value): CapturedTurn => {
    const turn = object(value);
    if (!turn) throw new Error('invalid captured turn');
    const owner_kind = turn.owner_kind;
    if (
      owner_kind !== 'ticket' &&
      owner_kind !== 'manager' &&
      owner_kind !== 'chief' &&
      owner_kind !== 'unknown'
    ) {
      throw new Error('invalid captured owner');
    }
    const status = turn.original_status;
    if (status !== 'running' && status !== 'intent') throw new Error('invalid unresolved status');
    const conversation_id = string(turn.conversation_id);
    const display_name = string(turn.display_name);
    const occurrence_id = string(turn.occurrence_id, true);
    const runtime_epoch = string(turn.runtime_epoch, true);
    if (occurrence_id === null && runtime_epoch === null)
      throw new Error('captured turn lacks exact identity');
    const claim = turn.claim ?? null;
    if (claim !== null && claim !== 'none' && claim !== 'out' && claim !== 'errored') {
      throw new Error('invalid captured claim');
    }
    return {
      conversation_id,
      display_name,
      occurrence_id,
      runtime_epoch,
      owner_kind,
      original_status: status,
      claim,
      turn_number: integer(turn.turn_number, 1),
      owner_id: string(turn.owner_id, true),
      claim_revision: turn.claim_revision == null ? null : integer(turn.claim_revision),
    };
  });
  return {
    version: CAPTURE_VERSION,
    deployment_id,
    capture_id,
    state,
    turns,
    captured_at: integer(raw.captured_at),
    detail: string(raw.detail, true),
  };
}
export function manifest_root(db_path: string): string {
  return join(dirname(resolve_path_sync(expand_path(db_path))), 'deploy-interruptions');
}
export function write_manifest(db_path: string, manifest: CaptureManifest): string {
  const payload = Buffer.from(JSON.stringify(manifest));
  manifest_from_dict(manifest);
  if (payload.length > MAX_BYTES) throw new Error('capture manifest exceeds size limit');
  const directory = join(manifest_root(db_path), identity(manifest.deployment_id));
  mkdirSync(directory, { recursive: true });
  if (lstatSync(directory).isSymbolicLink() || lstatSync(dirname(directory)).isSymbolicLink()) {
    throw new Error('capture directory must not be a symlink');
  }
  const target = join(directory, identity(manifest.capture_id) + '.json');
  const temporary = join(directory, '.capture-' + randomUUID());
  try {
    const descriptor = openSync(temporary, 'wx', 0o600);
    try {
      writeFileSync(descriptor, payload);
      fsyncSync(descriptor);
    } finally {
      closeSync(descriptor);
    }
    renameSync(temporary, target);
    const directory_descriptor = openSync(directory, constants.O_RDONLY | constants.O_DIRECTORY);
    try {
      fsyncSync(directory_descriptor);
    } finally {
      closeSync(directory_descriptor);
    }
  } finally {
    rmSync(temporary, { force: true });
  }
  return target;
}
export function read_manifests(db_path: string): readonly CaptureManifest[] {
  const root = manifest_root(db_path);
  let directories: string[];
  try {
    if (lstatSync(root).isSymbolicLink()) return [];
    directories = readdirSync(root).sort();
  } catch {
    return [];
  }
  const manifests: CaptureManifest[] = [];
  const unavailable = (
    deployment_id: string,
    capture_id: string,
    detail: string,
  ): CaptureManifest => ({
    version: CAPTURE_VERSION,
    deployment_id,
    capture_id,
    captured_at: 0,
    state: 'unavailable',
    detail,
    turns: [],
  });
  for (const name of directories) {
    let deployment_id: string;
    try {
      deployment_id = identity(name);
    } catch {
      continue;
    }
    const directory = join(root, name);
    let paths: string[];
    try {
      const info = lstatSync(directory);
      if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('unsafe capture directory');
      paths = readdirSync(directory)
        .filter((path) => path.endsWith('.json'))
        .sort();
    } catch {
      manifests.push(
        unavailable(
          deployment_id,
          'unreadable-directory',
          'The interruption account directory is unreadable.',
        ),
      );
      continue;
    }
    for (const path of paths) {
      let capture_id: string;
      try {
        capture_id = identity(path.slice(0, -5));
      } catch {
        continue;
      }
      let manifest: CaptureManifest;
      try {
        const descriptor = openSync(
          join(directory, path),
          constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW,
        );
        let payload: Buffer;
        try {
          const info = fstatSync(descriptor);
          if (!info.isFile() || info.size > MAX_BYTES) throw new Error('unsafe capture file');
          const buffer = Buffer.alloc(Math.min(info.size + 1, MAX_BYTES + 1));
          let count = 0;
          let read = 0;
          do {
            read = readSync(descriptor, buffer, count, buffer.length - count, null);
            count += read;
          } while (read > 0 && count < buffer.length);
          if (count > MAX_BYTES) throw new Error('capture file exceeds size limit');
          payload = buffer.subarray(0, count);
        } finally {
          closeSync(descriptor);
        }
        manifest = manifest_from_dict(
          JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(payload)),
        );
        if (manifest.deployment_id !== deployment_id || manifest.capture_id !== capture_id) {
          throw new Error('capture path and identities differ');
        }
      } catch {
        manifest = unavailable(
          deployment_id,
          capture_id,
          'The interruption account is unreadable or invalid.',
        );
      }
      manifests.push(manifest);
    }
  }
  return manifests;
}
