/** Export-only companions used by the existing Python direct-comparison route. */
import { readFileSync, statSync } from 'node:fs';
import { FileUpdateAttemptStore } from '../attempts.ts';
import { deployment_status, resolve, settle } from '../logic/experience.ts';
import { attempt_to_dict, parse_attempt, parse_release } from '../codec.ts';
import type { UpdateAttempt } from '../contracts.ts';
export {
  probe_service,
  probe_continuations,
  probe_update_routes,
  probe_node_frontier,
} from './service.ts';
export { probe_update_provider } from './provider.ts';
import { numeric_value } from '../../../core/http/numeric_json.ts';
import type {
  ContendedStoreRequest,
  StoreProbeAnswer,
  StoreProbeRequest,
  StoreProbeResult,
} from './contracts.ts';

function answer(work: () => unknown): StoreProbeAnswer {
  try {
    return { result: work(), error: null };
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    return { result: null, error: { type: error.constructor.name, message: error.message } };
  }
}
export function probe_store(request: StoreProbeRequest): StoreProbeResult {
  let emitted = 0;
  const store = new FileUpdateAttemptStore(
    request.path,
    () => emitted++,
    () => request.now,
  );
  const answers = request.operations.map((operation) =>
    answer(() => {
      if (operation.kind === 'read') {
        const current = store.read();
        return current === null ? null : attempt_to_dict(current);
      }
      if (operation.kind === 'create')
        return attempt_to_dict(store.create(parse_attempt(operation.attempt)));
      if (operation.kind === 'import_legacy') {
        const adopted = store.import_legacy_request(
          operation.data_directory!,
          operation.running_sha!,
        );
        return adopted === null ? null : attempt_to_dict(adopted);
      }
      if (operation.kind === 'change')
        return attempt_to_dict(
          store.change(
            operation.attempt_id!,
            new Set(operation.expected),
            (current) => ({ ...current, ...operation.patch }) as UpdateAttempt,
          ),
        );
      store.remove(operation.attempt_id!, new Set(operation.expected));
      return null;
    }),
  );
  let file_text: string | null = null;
  let file_mode: number | null = null;
  try {
    file_text = readFileSync(request.path, 'utf8');
    file_mode = statSync(request.path).mode & 0o777;
  } catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') throw error;
  }
  return { answers, file_text, file_mode, emitted };
}
export async function probe_contended_change(request: ContendedStoreRequest) {
  const store = new FileUpdateAttemptStore(
    request.path,
    () => {},
    () => request.now,
  );
  const started = performance.now();
  const timer = new Promise<number>((resolve) =>
    setTimeout(() => resolve(performance.now() - started), 0),
  );
  const result = answer(() =>
    attempt_to_dict(
      store.change(request.attempt_id, new Set(request.expected), (current) => ({
        ...current,
        detail: 'Node won',
      })),
    ),
  );
  const synchronous_ms = performance.now() - started;
  return { ...result, synchronous_ms, timer_delay_ms: await timer };
}

export function probe_experience(cases: import('./contracts.ts').ExperienceCase[]) {
  return cases.map((item) => {
    try {
      const attempt = item.attempt === null ? null : parse_attempt(item.attempt);
      const available = item.available === null ? null : parse_release(item.available);
      const settled = attempt === null ? null : settle(attempt, item.app_sha, item.now);
      return {
        resolve: resolve(attempt, available, item.app_sha, {
          ...item,
          mac_report_seconds: numeric_value(item.mac_report_seconds)!,
        }),
        settle: settled === null ? null : attempt_to_dict(settled),
        deployment: deployment_status(attempt, item.app_sha, {
          ...item,
          success_seconds: numeric_value(item.success_seconds)!,
        }),
      };
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      return { error: error.message };
    }
  });
}
