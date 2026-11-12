/** Current-Python comparison companion. No listener, child process, or provider. */
import { createDatabase } from '../../../core/database/index.ts';
import {
  reconcile,
  select_recoveries,
  bind_delivery,
  settle_delivery,
  read_summary,
  admit_recovery,
} from '../data.ts';
import { manifest_from_dict, manifest_root, read_manifests } from '../files.ts';
import { relative } from 'node:path';
import { makeUsageService } from '../../usage/composition.ts';
import { VideoUploadService } from '../../video_uploads/service.ts';
import type { Config } from '../../../core/config.ts';
import type { LifecycleStatus } from '../../record/lifecycle.ts';
interface Spec {
  readonly cases: readonly { path: string; status: LifecycleStatus | 'restart' }[];
  readonly manifest: unknown;
}
export function inspect_capture_evidence(db_path: string, fixture_root: string) {
  const manifests = read_manifests(db_path);
  const database = createDatabase(db_path);
  try {
    const usage = makeUsageService({ db_path, test_mode: true } as Config, {
      ratesFetcher: () => null,
      transcriptDirs: [],
    });
    const video = new VideoUploadService(database, db_path);
    database.write((tx) => reconcile(tx, manifests, 100));
    return {
      folders: {
        usage: relative(fixture_root, usage.stateDir),
        deploy: relative(fixture_root, manifest_root(db_path)),
        video: relative(fixture_root, video.root),
      },
      manifests,
      accounts: database.read(read_summary),
      rows: database.read((sql) =>
        sql.all('SELECT * FROM deploy_interruption_accounts ORDER BY capture_id'),
      ),
      eligible: Object.fromEntries(database.read(select_recoveries)),
    };
  } finally {
    database.close();
  }
}
export function run(spec: Spec) {
  return spec.cases.map((entry) => {
    const database = createDatabase(entry.path);
    try {
      const manifest = manifest_from_dict(spec.manifest);
      database.write((tx) => reconcile(tx, [manifest], 100));
      const selected = Object.fromEntries(database.read(select_recoveries));
      const notice = database.write((tx) =>
        bind_delivery(
          tx,
          'c',
          2,
          selected.c,
          new Map(selected.c.map((ref) => [ref.interruption_id, 'sender'])),
        ),
      );
      const refusal = database.write((tx) => admit_recovery(tx, 'c', selected.c, 100));
      if (refusal !== null) throw new Error(refusal);
      if (entry.status === 'restart') database.write((tx) => reconcile(tx, [manifest], 101));
      else
        database.write((tx) =>
          settle_delivery(
            tx,
            'c',
            [
              {
                kind: 'attempt',
                identity: 'prompt:2',
                turn_number: 2,
                operation: 'prompt',
                status: entry.status as LifecycleStatus,
                provider_identity: null,
              },
            ],
            { now: 101 },
          ),
        );
      return {
        selected,
        notice,
        accounts: database.read(read_summary),
        deliveries: database.read((sql) =>
          sql.all(
            'SELECT * FROM deploy_interruption_deliveries ORDER BY interruption_id,conversation_id,turn_number',
          ),
        ),
        eligible: Object.fromEntries(database.read(select_recoveries)),
        epoch_rotated: database.read(
          (sql) =>
            sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch')!.epoch !==
            'old-epoch',
        ),
        claims: database.read((sql) =>
          sql.all(
            'SELECT id,worker_step_claim,worker_step_claim_revision FROM tickets ORDER BY id',
          ),
        ),
      };
    } finally {
      database.close();
    }
  });
}
