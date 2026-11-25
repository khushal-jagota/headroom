/** The offer and the press use current activity without replacing a failed machine target. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';
import type { ConversationSystem } from '../../conversation/contracts.ts';
import type { Database } from '../../core/database/contracts.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { FileUpdateAttemptStore } from './attempts.ts';
import type { MachineUpdateState, PublishedUpdate } from './contracts.ts';
import { UpdateService } from './service.ts';
import { attempt_body } from './routes.ts';

test('a newer offer retains machine failure and recomputes interruption at the press', async () => {
  const root = mkdtempSync(join(tmpdir(), 'update-impact-'));
  const machine: MachineUpdateState = {
    attempt_id: 'retained-machine-attempt',
    machine_id: '11111111-1111-4111-8111-111111111111',
    name: 'Linux',
    platform: 'linux',
    present: false,
    build_id: 'a'.repeat(64),
    target_build_id: 'b'.repeat(64),
    changed: true,
    state: 'failed',
    request_id: 'old-machine-request',
    detail: 'Failed',
    can_retry: true,
  };
  const release: PublishedUpdate = {
    publication_run_id: 1,
    artifact_id: 1,
    proof_artifact_id: 2,
    manifest: {
      app_sha: 'b'.repeat(40),
      source_digest: 'c'.repeat(64),
      artifact_digest: 'd'.repeat(64),
      runtime_contract: null,
    },
    archive_sha256: 'e'.repeat(64),
    schema_floor: '',
    expires_at: null,
    mac: null,
  };
  const interrupted = vi.fn().mockReturnValue(['First agent']);
  const attempts = new FileUpdateAttemptStore(join(root, 'attempt.json'), () => {});
  const available = vi
    .fn<() => Promise<PublishedUpdate | null>>()
    .mockRejectedValueOnce(new Error('catalog unavailable'))
    .mockResolvedValue(null);
  const service = new UpdateService({
    provider: { available, download_artifact: async () => {} },
    attempts,
    runtime: {
      database: {} as Database,
      conversation: () =>
        ({
          workspace_activity: () => 'busy',
          update_interrupted_work: interrupted,
        }) as unknown as ConversationSystem,
      assert_database_owned: () => {},
    },
    changes: { emit: () => {} },
    current_root: root,
    database: join(root, 'db'),
    backup_directory: root,
    current_app_sha: 'a'.repeat(40),
    health_url: '',
    service_unit: '',
    staging: { stage: async () => root },
    installer: {
      node_schema_steps_pending: () => false,
      prepare: () => [],
      launch: async () => {},
    },
    mac: {
      machine_states: (offer) => [
        {
          ...machine,
          attempt_id: offer ? null : machine.attempt_id,
          request_id: offer ? null : machine.request_id,
          state: offer ? 'available' : 'failed',
          can_retry: !offer,
        },
      ],
      retry_machine: () => {},
      prepare: async () => {},
    },
  });
  vi.spyOn(service as unknown as { launch(): void }, 'launch').mockImplementation(() => {});
  try {
    service.start();
    expect(service.status()).toMatchObject({
      state: 'not_checked',
      activity: 'busy',
      allowed_actions: [],
    });
    await expect(service.prefetch()).rejects.toThrow('catalog unavailable');
    expect(service.status().state).toBe('check_unavailable');
    await service.prefetch();
    expect(service.status().state).toBe('up_to_date');
    vi.spyOn(service, 'ready_release').mockReturnValue(release);
    const offered = service.status();
    expect(offered).toMatchObject({
      state: 'available',
      activity: 'busy',
      allowed_actions: ['update'],
    });
    expect(offered.machines[0]).toMatchObject({
      state: 'failed',
      can_retry: true,
      request_id: 'old-machine-request',
    });
    expect(offered.available_machines[0]?.state).toBe('available');
    expect(offered.interruption.agents).toEqual(['First agent']);
    expect(interrupted).toHaveBeenLastCalledWith([HOME_MACHINE_ID, machine.machine_id]);
    interrupted.mockReturnValue(['Agent that started after the offer']);
    const pressed = service.install(release.manifest.app_sha);
    expect(attempt_body(pressed).interruption).toEqual({
      stops_agents: true,
      agents: ['Agent that started after the offer'],
      machine_ids: [HOME_MACHINE_ID, machine.machine_id],
    });
  } finally {
    await service.stop({ deadline: performance.now() + 30_000 });
    rmSync(root, { recursive: true, force: true });
  }
});
