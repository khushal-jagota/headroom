/** Frozen source admission and fresh, actual-use credential resolution. */
import type { Database } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { FileCredentialCipher, DatabaseCredentialResolver } from '../credentials/store.ts';
import type { CredentialResolver } from '../credentials/contracts.ts';
import { latest_observations } from '../measures/data.ts';
import type { RunAdmission, RuntimeLimits, SourceRun } from './contracts.ts';
import { DEFAULT_LIMITS } from './configuration.ts';
import { current_definition } from './run_actions.ts';
import { read_source } from './data.ts';
export function prepare_admission(
  database: Database,
  run: SourceRun,
  { key_path, limits = DEFAULT_LIMITS }: { key_path: string; limits?: RuntimeLimits },
): [RunAdmission, CredentialResolver] {
  const admission = database.read((sql) => {
    if (!current_definition(sql, run))
      throw new PlannerError('stale_claim', 'source admission changed');
    const script = read_source(sql, run.source_id).active_script!;
    return {
      run,
      script_text: script.text,
      previous: latest_observations(
        sql,
        run.required_outputs.map((x) => x.measure_id),
      ),
      limits,
      deadline_monotonic: performance.now() / 1000 + limits.wall_seconds,
    };
  });
  const credentials: CredentialResolver = {
    resolve(reference) {
      return database.read((sql) =>
        new DatabaseCredentialResolver(sql, new FileCredentialCipher(key_path, true)).resolve(
          reference,
        ),
      );
    },
  };
  return [admission, credentials];
}
