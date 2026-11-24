import { PROTOCOL_VERSION } from '../../../execution_configuration.ts';
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
    protocol_version: PROTOCOL_VERSION,
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

// Earlier report-admission coverage does not prove independent machine requests,
// restart recovery, or server-side suppression of unchanged platform downloads.
import { mkdirSync, writeFileSync } from 'node:fs';
import { afterEach } from 'vitest';
import type { MachineUpdateMachine, PublishedUpdate, UpdateAttempt } from '../contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../../../execution_identity.ts';
import { runtime_archive } from '../../../executor/mac/updates/testing/runtime_archive.ts';

const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => {
  for (const cleanup of cleanups.splice(0)) await cleanup();
});
function machine_fixture(platform = 'darwin') {
  const root = mkdtempSync(join(tmpdir(), 'machine-independent-'));
  const children = new ChildProcesses();
  const controller = new AbortController();
  const revision = 'b'.repeat(40);
  const archive = runtime_archive(
    revision,
    false,
    platform === 'linux'
      ? { system: 'linux', architecture: 'x86_64', role: 'executor' }
      : undefined,
  );
  const build = 'd'.repeat(64);
  const release: PublishedUpdate = {
    artifact_source: 'github-release',
    publication_run_id: 1,
    artifact_id: 1,
    proof_artifact_id: 2,
    manifest: {
      app_sha: revision,
      source_digest: 'e'.repeat(64),
      artifact_digest: 'f'.repeat(64),
      runtime_contract: null,
    },
    archive_sha256: archive.sha,
    schema_floor: 'head',
    expires_at: null,
    mac:
      platform === 'darwin'
        ? { artifact_id: 3, archive_sha256: archive.sha, archive_size: archive.bytes.length }
        : null,
    linux_executor:
      platform === 'linux'
        ? { artifact_id: 4, archive_sha256: archive.sha, archive_size: archive.bytes.length }
        : null,
    parts: {
      server: { app_sha: revision, artifact_digest: 'f'.repeat(64) },
      screens: { sha256: 'e'.repeat(64) },
      machines: {
        [platform === 'linux' ? 'linux-x86_64' : 'darwin-arm64']: {
          build_id: build,
          archive_sha256: archive.sha,
        },
      },
    },
  };
  const identity = parse_identity({
    application_revision: 'a'.repeat(40),
    build_id: build,
    protocol_version: PROTOCOL_VERSION,
    spool_format_version: 5,
    executor_incarnation: '11111111-1111-4111-8111-111111111111',
  });
  let machines: MachineUpdateMachine[] = [
    { machine_id: ORIGINAL_MAC_MACHINE_ID, name: 'Machine', platform, present: true, identity },
  ];
  const attempts = new FileUpdateAttemptStore(join(root, 'attempt.json'), () => {});
  const context = {
    root,
    attempts,
    provider: {
      available: async () => null,
      download_artifact: vi.fn(async () => {
        throw new Error('unexpected download');
      }),
    },
    current_app_sha: revision,
    child_processes: children,
    signal: controller.signal,
    ready_release: () => release,
    emit_change: () => {},
    machines: () => machines,
    platform_for: (machine_id: string) =>
      machines.find((machine) => machine.machine_id === machine_id)?.platform ?? platform,
  };
  const service = new MacUpdateService(context);
  cleanups.push(async () => {
    controller.abort();
    await service.settled();
    children.seal();
    await children.settled();
    rmSync(root, { recursive: true, force: true });
  });
  const attempt: UpdateAttempt = {
    attempt_id: 'attempt',
    from_sha: 'a'.repeat(40),
    to_sha: revision,
    mode: 'now',
    confirm_activity: true,
    state: 'done',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    release,
    detail: null,
    deadline_at: null,
    backup: null,
    snapshot_required: false,
    steps: {},
    mac_attempt_id: 'machine-request',
    extra: {},
  };
  return {
    root,
    context,
    service,
    release,
    identity,
    attempts,
    attempt,
    archive,
    set_machines(value: MachineUpdateMachine[]) {
      machines = value;
    },
    cache() {
      const path = join(root, platform === 'linux' ? 'linux/archives' : 'archives');
      mkdirSync(path, { recursive: true });
      writeFileSync(join(path, archive.sha + '.tar.gz'), archive.bytes);
    },
  };
}

test('equal builds and an empty machine registry skip archive download and machine target', async () => {
  const f = machine_fixture();
  await f.service.prepare(f.release);
  f.attempts.create(f.attempt);
  expect(f.service.check()).toMatchObject({ target: null, prefetch: null });
  expect(f.service.machine_states()[0]).toMatchObject({ state: 'current', changed: false });
  expect(f.service.machine_states(f.release)[0]).toMatchObject({
    attempt_id: null,
    request_id: null,
  });
  expect(() => f.service.retry_machine(ORIGINAL_MAC_MACHINE_ID, f.attempt.attempt_id)).toThrow(
    'already has the target build',
  );
  f.set_machines([]);
  await f.service.prepare(f.release);
  expect(f.context.provider.download_artifact).not.toHaveBeenCalled();
});

test('a known publication build requires a matching machine build even at the same revision', () => {
  const f = machine_fixture();
  delete f.identity.build_id;
  f.identity.application_revision = f.release.manifest.app_sha;
  expect(f.service.machine_states(f.release)[0]?.changed).toBe(true);
});

test.each(['darwin', 'linux'])(
  'a legacy %s target clears after the machine reports its revision, including after restart',
  async (platform) => {
    const f = machine_fixture(platform);
    delete f.release.parts;
    const machine_id = '33333333-3333-4333-8333-333333333333';
    f.set_machines([
      { machine_id, name: 'Machine', platform, present: true, identity: f.identity },
    ]);
    f.attempts.create(f.attempt);
    expect(f.service.machine_states()[0]).toMatchObject({ state: 'updating', changed: true });
    f.identity.application_revision = f.release.manifest.app_sha;
    const reconstructed = new MacUpdateService(f.context);
    for (const service of [f.service, reconstructed]) {
      expect(service.machine_states()[0]).toMatchObject({
        state: 'current',
        changed: false,
        target_build_id: null,
        can_retry: false,
        detail: null,
      });
      expect(service.machine_states(f.release)[0]).toMatchObject({
        state: 'current',
        changed: false,
      });
      await service.prepare(f.release);
      expect(service.for_machine(machine_id).check()).toMatchObject({
        target: null,
        prefetch: null,
      });
    }
    f.set_machines([{ machine_id, name: 'Machine', platform, present: false, identity: null }]);
    expect(reconstructed.machine_states()[0]).toMatchObject({ state: 'waiting', changed: true });
    expect(f.context.provider.download_artifact).not.toHaveBeenCalled();
    await reconstructed.settled();
  },
);

test('retry changes only the selected machine request and survives service reconstruction', async () => {
  const f = machine_fixture();
  delete f.identity.build_id;
  const other = '22222222-2222-4222-8222-222222222222';
  f.set_machines([
    {
      machine_id: ORIGINAL_MAC_MACHINE_ID,
      name: 'One',
      platform: 'darwin',
      present: false,
      identity: f.identity,
    },
    { machine_id: other, name: 'Two', platform: 'darwin', present: false, identity: f.identity },
  ]);
  f.attempts.create(f.attempt);
  const before = f.service.machine_states();
  expect(before[1]?.request_id).toMatch(/^[A-Za-z0-9_-]+$/);
  f.service.retry_machine(other, f.attempt.attempt_id);
  const after = f.service.machine_states();
  expect(after[0]?.request_id).toBe(before[0]?.request_id);
  expect(after[1]?.request_id).not.toBe(before[1]?.request_id);
  const reconstructed = new MacUpdateService(f.context);
  expect(reconstructed.machine_states().map((machine) => machine.request_id)).toEqual(
    after.map((machine) => machine.request_id),
  );
  // A later failed server attempt does not erase the last activated target for away machines.
  f.attempts.change(f.attempt.attempt_id, new Set(['done']), (attempt) => ({
    ...attempt,
    state: 'failed',
  }));
  expect(reconstructed.machine_states()[1]?.request_id).toBe(after[1]?.request_id);
  await reconstructed.settled();
});

test('Linux validates retained archive bytes after restart and finishes acknowledgment after the new lease', async () => {
  const f = machine_fixture('linux');
  delete f.identity.build_id;
  f.attempts.create(f.attempt);
  f.cache();
  // The original id's platform is Darwin in legacy root composition. Use another registered Linux machine.
  const linux_id = '33333333-3333-4333-8333-333333333333';
  f.set_machines([
    { machine_id: linux_id, name: 'Linux', platform: 'linux', present: true, identity: f.identity },
  ]);
  const linux = f.service.for_machine(linux_id);
  linux.check();
  await f.service.settled();
  const first = linux.check().target!;
  expect(first?.activate).toBe(true);
  linux.report(
    {
      request_id: first.request_id,
      application_revision: first.application_revision,
      phase: 'ready_to_restart',
      identity: f.identity,
    },
    { presence: 'present', identity: f.identity },
  );
  const reconstructed = new MacUpdateService(f.context);
  const restored = reconstructed.for_machine(linux_id);
  await reconstructed.settled();
  f.identity.build_id = first.build_id;
  f.identity.application_revision = first.application_revision;
  expect(reconstructed.machine_states()[0]?.state).toBe('updating');
  const target = restored.check().target;
  expect(target?.request_id).toBe(first.request_id);
  restored.report(
    {
      request_id: first.request_id,
      application_revision: first.application_revision,
      phase: 'current',
      identity: f.identity,
    },
    { presence: 'present', identity: f.identity },
  );
  expect(restored.check().target).toBeNull();
  expect(reconstructed.machine_states()[0]?.state).toBe('current');
  expect(f.context.provider.download_artifact).not.toHaveBeenCalled();
  await reconstructed.settled();
});

test('machine archive failure survives restart, preserves other publication errors, and retry recovers only that target', async () => {
  const f = machine_fixture();
  delete f.identity.build_id;
  await expect(f.service.prepare(f.release)).resolves.toBeUndefined();
  expect(f.service.machine_states(f.release)[0]).toMatchObject({
    state: 'failed',
    detail: 'unexpected download',
  });
  f.attempts.create(f.attempt);
  const future = structuredClone(f.release);
  future.mac!.archive_sha256 = '9'.repeat(64);
  await f.service.prepare(future);
  const reconstructed = new MacUpdateService(f.context);
  expect(reconstructed.machine_states()[0]).toMatchObject({
    state: 'failed',
    can_retry: true,
    detail: 'unexpected download',
  });
  expect(reconstructed.check().target).toBeNull();
  f.cache();
  reconstructed.retry_machine(ORIGINAL_MAC_MACHINE_ID, f.attempt.attempt_id);
  await reconstructed.settled();
  expect(reconstructed.check().target?.activate).toBe(true);
  expect(reconstructed.machine_states()[0]?.state).toBe('updating');
  expect(reconstructed.machine_states(future)[0]?.state).toBe('failed');
});

test('a missing platform payload is unavailable and cannot be retried as an archive failure', () => {
  const f = machine_fixture();
  f.release.mac = null;
  f.attempts.create(f.attempt);
  expect(f.service.machine_states()[0]).toMatchObject({ state: 'unavailable', can_retry: false });
  expect(() => f.service.retry_machine(ORIGINAL_MAC_MACHINE_ID, f.attempt.attempt_id)).toThrow();
});
