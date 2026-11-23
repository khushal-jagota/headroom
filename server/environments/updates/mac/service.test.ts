/** Reports must prove the leased content identity without rewriting release provenance. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { parse_identity } from '../../../execution_identity.ts';
import { FileUpdateAttemptStore } from '../attempts.ts';
import type { MacUpdateTarget } from './contracts.ts';
import { MacUpdateService } from './service.ts';

test('current accepts matching leased content across revisions and rejects unleased content claims', async () => {
  const root = mkdtempSync(join(tmpdir(), 'machine-current-'));
  const children = new ChildProcesses();
  const service = new MacUpdateService({
    root,
    attempts: new FileUpdateAttemptStore(join(root, 'attempt.json'), () => {}),
    provider: null,
    current_app_sha: 'a'.repeat(40),
    child_processes: children,
    signal: new AbortController().signal,
    ready_release: () => null,
    emit_change: () => {},
  });
  const target: MacUpdateTarget = {
    application_revision: 'a'.repeat(40),
    build_id: 'd'.repeat(64),
    request_id: 'candidate',
    activate: true,
    archive_path: '/api/executor/update/candidate/archive',
    archive_sha256: 'e'.repeat(64),
    archive_size: 1,
    mode: 'idle',
  };
  // Target selection has separate archive coverage. This proof isolates report admission.
  vi.spyOn(service as unknown as { target(): MacUpdateTarget }, 'target').mockReturnValue(target);
  const identity = parse_identity({
    application_revision: 'b'.repeat(40),
    build_id: target.build_id,
    protocol_version: 26,
    spool_format_version: 5,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
  });
  const report = {
    request_id: target.request_id,
    application_revision: target.application_revision,
    phase: 'current' as const,
    identity,
  };
  try {
    expect(service.report(report, { presence: 'present', identity }).phase).toBe('current');
    expect(identity.application_revision).toBe('b'.repeat(40));
    expect(() =>
      service.report(report, {
        presence: 'present',
        identity: { ...identity, build_id: 'f'.repeat(64) },
      }),
    ).toThrow('not the present, leased one');
    expect(() =>
      service.report(
        { ...report, identity: { ...identity, build_id: 'f'.repeat(64) } },
        { presence: 'present', identity },
      ),
    ).toThrow();
    expect(() => parse_identity({ ...identity, build_id: 'invalid' })).toThrow();
  } finally {
    children.seal();
    await children.settled();
    rmSync(root, { recursive: true, force: true });
  }
});
