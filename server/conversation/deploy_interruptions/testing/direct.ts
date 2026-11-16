/** Current-Python comparison companion. No listener, child process, or provider. */
import { createDatabase } from '../../../core/database/index.ts';
import { InMemoryConversationSystem } from '../../in_memory_conversation_system.ts';
import {
  PromptDeliveryStarted,
  PromptDeliveryRefused,
  PromptDeliveryUncertain,
  PromptDeliveryRefusalReason,
} from '../../contracts.ts';
import { AutomaticStartAdmission } from '../../start_admission/index.ts';
import { ConversationStore } from '../../record/data/store.ts';
import { HOME_MACHINE_ID } from '../../../execution_identity.ts';
import { recover_conversations } from '../coordinator.ts';
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

/** Refusal during closure must not consume the source evidence for the next admitted pass. */
export async function run_recovery_closure(spec: {
  path: string;
  manifest: unknown;
  canceled_first?: boolean;
  paced?: boolean;
  already_running?: boolean;
}) {
  const database = createDatabase(spec.path);
  try {
    database.write((tx) => reconcile(tx, [manifest_from_dict(spec.manifest)], 100));
    let now = 0;
    const starts = new AutomaticStartAdmission({ clock: () => now });
    if (spec.paced) {
      starts.try_start({ identity: 'prior', destination: HOME_MACHINE_ID, work_class: 'worker' });
      starts.finish('prior');
    }
    const trace: unknown[][] = [];
    for (const name of ['try_start', 'finish', 'finish_unbound', 'uncertain'] as const) {
      const native = starts[name].bind(starts);
      Object.defineProperty(starts, name, {
        value: (argument: string | { identity: string; work_class: string }) => {
          const value = Reflect.apply(native, starts, [argument]);
          trace.push([
            name,
            typeof argument === 'string' ? argument : argument.identity,
            typeof argument === 'string' ? null : argument.work_class,
            value ?? null,
          ]);
          if (name === 'try_start') now++;
          return value;
        },
      });
    }
    const store = new ConversationStore(database);
    const system = Object.assign(new InMemoryConversationSystem(), {
      start_admission: starts,
      automatic_start_request: async (id: string, conversation_id: string) =>
        store.automatic_start_request(id, conversation_id),
    });
    let offers = 0;
    const offered_with_admission: boolean[] = [];
    const offered_ids: string[] = [];
    system.is_running = async () => spec.already_running ?? false;
    system.send = async (conversation_id) => {
      offers++;
      offered_ids.push(conversation_id);
      offered_with_admission.push(system.workspace_admission.active);
      if (spec.paced)
        return conversation_id === 'c2'
          ? new PromptDeliveryUncertain()
          : new PromptDeliveryRefused({
              refusal_reason: PromptDeliveryRefusalReason.no_such_conversation,
            });
      if (spec.canceled_first) {
        if (offers === 1) throw new DOMException('one recovery canceled', 'AbortError');
        return new PromptDeliveryStarted();
      }
      system.workspace_admission.close_starts();
      throw new Error('Workspace admission is closed for an owner update.');
    };
    await recover_conversations(system, database, { startup: true });
    const snapshot = () => ({
      offers,
      trace,
      snapshots: starts.snapshots(),
      offered_with_admission,
      offered_ids,
      active: system.workspace_admission.active,
      starts_closed: system.workspace_admission.starts_closed,
      pending: Object.fromEntries(database.read(select_recoveries)),
      rows: database.read((sql) =>
        sql.all('SELECT * FROM deploy_interruptions ORDER BY interruption_id'),
      ),
      deliveries: database.read((sql) =>
        sql.all('SELECT * FROM deploy_interruption_deliveries ORDER BY interruption_id'),
      ),
      claims: database.read((sql) =>
        sql.all('SELECT id,worker_step_claim,worker_step_claim_revision FROM tickets ORDER BY id'),
      ),
    });
    const after_refusal = snapshot();
    if (!spec.canceled_first && !spec.paced && !spec.already_running)
      await recover_conversations(system, database, { startup: true });
    return { after_refusal, after_denied_pass: snapshot() };
  } finally {
    database.close();
  }
}
