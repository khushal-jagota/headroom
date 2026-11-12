/** Canonical responsibility transfer used by Goal deletion; source execution stays separate. */
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { create_wake } from '../manager_wakes/data.ts';
const failure_messages: Record<string, string> = {
  runtime_failure: 'The source script failed.',
  malformed_protocol: 'The source sent an invalid host message.',
  credential_unavailable: 'A required credential is unavailable.',
  transport_failure: 'The provider request failed.',
  http_error: 'The provider returned an HTTP error.',
  invalid_json: 'The provider returned invalid JSON.',
  invalid_output: 'The source returned invalid output.',
  stale_definition: 'The saved source script changed during the run.',
  budget_exhausted: 'The run exceeded its execution timeout.',
  interrupted: 'The collection stopped before completion.',
};
export function failure_message(
  f: {
    code: string;
    budget: string | null;
    http_status: number | null;
    exception_type?: string | null;
    source_line?: number | null;
  } | null,
): string | null {
  if (!f) return null;
  let message = failure_messages[f.code]!;
  if (f.budget !== null) message += ` Limit: ${f.budget}.`;
  if (f.http_status !== null) message += ` HTTP status: ${f.http_status}.`;
  if (f.exception_type != null) message += ` ${f.exception_type}.`;
  if (f.source_line != null) message += ` Source line: ${f.source_line}.`;
  return message;
}
function repr(value: string): string {
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  let result = quote;
  for (const character of value) {
    if (character === quote || character === '\\') result += '\\' + character;
    else if (character === '\n') result += '\\n';
    else if (character === '\r') result += '\\r';
    else if (character === '\t') result += '\\t';
    else if (character !== ' ' && /[\p{C}\p{Z}]/u.test(character)) {
      const code = character.codePointAt(0)!;
      result +=
        code < 256
          ? '\\x' + code.toString(16).padStart(2, '0')
          : code < 65536
            ? '\\u' + code.toString(16).padStart(4, '0')
            : '\\U' + code.toString(16).padStart(8, '0');
    } else result += character;
  }
  return result + quote;
}
export function notify_episode(
  sql: TransactionSql,
  source: Record<string, unknown>,
  now: number,
): void {
  const episode = sql.get<{ episode: number; latest_run_id: string }>(
    'SELECT episode,latest_run_id FROM measure_source_failure_episodes WHERE source_id=? AND closed_at IS NULL',
    [String(source.id)],
  );
  if (!episode) return;
  const run = sql.get<{
    id: string;
    failure: string | null;
    required_outputs: string;
    dataset_outputs: string;
  }>('SELECT id,failure,required_outputs,dataset_outputs FROM measure_source_runs WHERE id=?', [
    episode.latest_run_id,
  ]);
  if (!run) throw new PlannerError('not_found', 'source run does not exist');
  const project = sql.get<{ name: string }>('SELECT name FROM projects WHERE id=?', [
      String(source.project_id),
    ]),
    success = sql.get<{ finished_at: number }>(
      "SELECT finished_at FROM measure_source_runs WHERE source_id=? AND mode='collection' AND status='succeeded' ORDER BY finished_at DESC,rowid DESC LIMIT 1",
      [String(source.id)],
    );
  const message = failure_message(run.failure ? JSON.parse(run.failure) : null) ?? 'None';
  const dates = [
    ...(JSON.parse(run.required_outputs) as { required_date: string }[]).slice(0, 5),
    ...(JSON.parse(run.dataset_outputs) as { required_date: string }[]).slice(0, 5),
  ].map((v) => v.required_date);
  const summary = `Measure source ${repr(String(source.name))} in ${project?.name ?? source.project_id} / ${source.account} failed. ${message} Last successful collection timestamp: ${success?.finished_at ?? 'none'}. Suggested collection dates: ${[...new Set(dates)].join(', ')}. Inspect source ${source.id} and run ${run.id}. Repair the source script with measure-source submit-script, then request-run to collect. Credential replacement requires owner API submission. If you cannot repair it, message the user through the existing conversation route. Only a successful collection restores source health.`;
  let target: Principal = { kind: 'chief', id: 'chief' };
  if (source.responsible_kind === 'manager')
    target = { kind: 'manager', id: String(source.responsible_manager_id) };
  if (source.responsible_kind === 'goal') {
    const goal = sql.get<{ holder_kind: string; holder_id: string | null }>(
      'SELECT holder_kind,holder_id FROM goals WHERE id=?',
      [String(source.responsible_goal_id)],
    );
    if (!goal) throw new PlannerError('not_found', 'goal not found');
    if (goal.holder_kind === 'manager') target = { kind: 'manager', id: goal.holder_id! };
  }
  create_wake(sql, {
    manager_id: target.kind === 'manager' ? target.id : null,
    ticket_id: null,
    source_kind: 'source_failure',
    source_revision: episode.episode,
    summary,
    now,
    target,
    source_identity: {
      source_id: String(source.id),
      episode: episode.episode,
      responsibility_revision: Number(source.responsibility_revision),
      recipient: target,
    },
  });
}
export function set_responsible(
  sql: TransactionSql,
  id: string,
  responsible: Principal,
  expected_revision: number,
  now: number,
): void {
  const source = sql.get<Record<string, unknown>>('SELECT * FROM measure_sources WHERE id=?', [id]);
  if (!source) throw new PlannerError('not_found', 'measure source does not exist');
  if (source.responsibility_revision !== expected_revision)
    throw new PlannerError('stale_claim', 'source responsibility changed');
  if (
    !['chief', 'manager', 'goal'].includes(responsible.kind) ||
    (responsible.kind === 'chief' && responsible.id !== 'chief')
  )
    throw new PlannerError('validation', 'a source needs Chief, Manager, or goal responsibility');
  if (
    responsible.kind === 'manager' &&
    !sql.get('SELECT 1 FROM managers WHERE id=?', [responsible.id])
  )
    throw new PlannerError('validation', 'responsible Manager does not exist');
  if (responsible.kind === 'goal' && !sql.get('SELECT 1 FROM goals WHERE id=?', [responsible.id]))
    throw new PlannerError('validation', 'responsible goal does not exist');
  const current_id =
    source.responsible_kind === 'chief'
      ? 'chief'
      : source.responsible_kind === 'manager'
        ? source.responsible_manager_id
        : source.responsible_goal_id;
  if (source.responsible_kind === responsible.kind && current_id === responsible.id) return;
  sql.run(
    `UPDATE measure_sources SET responsible_kind=?,responsible_manager_id=?,responsible_goal_id=?,
 responsibility_revision=responsibility_revision+1,updated_at=? WHERE id=?`,
    [
      responsible.kind,
      responsible.kind === 'manager' ? responsible.id : null,
      responsible.kind === 'goal' ? responsible.id : null,
      now,
      id,
    ],
  );
  notify_episode(
    sql,
    sql.get<Record<string, unknown>>('SELECT * FROM measure_sources WHERE id=?', [id])!,
    now,
  );
}
