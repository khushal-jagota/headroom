/** Plan-limit policy over an injected read of the Claude CLI's own `get_usage` answer. */
import { parse_backend_instance_id } from '../../core/identity.ts';
import type {
  BackendUsageAdapter,
  BackendUsageResult,
  BackendUsageWindow,
  BackendUsageWindowKind,
} from '../../../conversation/backend_usage/contracts.ts';
import { is_record as object } from '../../../core/records.ts';
import {
  parse_datetime,
  percentage,
  usage_window,
} from '../../../conversation/backend_usage/logic/windows.ts';

/** The CLI's raw `get_usage` answer. The source owns the short-lived SDK session. */
export interface ClaudeUsageSource {
  (options: { signal?: AbortSignal }): Promise<unknown>;
}
export class ClaudeUsageAdapter implements BackendUsageAdapter {
  private readonly read_usage: ClaudeUsageSource;
  private readonly now: () => Date;
  constructor(options: { read_usage: ClaudeUsageSource; now: () => Date }) {
    this.read_usage = options.read_usage;
    this.now = options.now;
  }
  async refresh(options: { signal?: AbortSignal } = {}): Promise<BackendUsageResult> {
    options.signal?.throwIfAborted();
    const result = (
      outcome: BackendUsageResult['outcome'],
      detail: string,
    ): BackendUsageResult => ({
      backend_key: parse_backend_instance_id('claude'),
      outcome,
      detail,
      observed_at: null,
      windows: [],
    });
    let answer: unknown;
    try {
      answer = await this.read_usage(options);
    } catch (error) {
      options.signal?.throwIfAborted();
      console.error('claude usage read failed', error);
      return result('failed', 'Claude could not be asked for its usage. Try again.');
    }
    if (!object(answer) || typeof answer.rate_limits_available !== 'boolean')
      return result('failed', 'Claude returned usage in a format Panels does not understand.');
    // The CLI reports no plan limits without a claude.ai login: none at all, an API key, a cloud
    // provider, or a login that predates the profile scope. `claude auth login` fixes each.
    if (!answer.rate_limits_available)
      return result(
        'unauthenticated',
        'Claude is not logged in to a Claude plan. Run `claude auth login` and try again.',
      );
    // Available but null: the CLI could not reach its usage endpoint this time.
    if (!object(answer.rate_limits))
      return result('failed', 'Claude could not refresh its usage. Try again.');
    const windows = claude_usage_windows(answer.rate_limits);
    if (!windows.length)
      return result('failed', 'Claude returned usage in a format Panels does not understand.');
    return {
      backend_key: parse_backend_instance_id('claude'),
      outcome: 'succeeded',
      detail: null,
      observed_at: this.now(),
      windows,
    };
  }
}

/** One entry of the usage endpoint's `limits` list: a session or weekly window, or a weekly
 * window scoped to the model it names. */
function claude_limit_entry(value: unknown): BackendUsageWindow | null {
  if (!object(value)) return null;
  const used_percent = percentage(value.percent) ?? percentage(value.utilization);
  const resets_at = parse_datetime(value.resets_at) ?? parse_datetime(value.reset_at);
  if (used_percent === null || resets_at === null) return null;
  const scope = object(value.scope) ? value.scope : null;
  const model = object(scope?.model) ? scope.model : null;
  let model_name = model?.display_name;
  if (typeof model_name !== 'string' || !model_name) model_name = model?.displayName;
  if (typeof model_name === 'string' && model_name)
    return usage_window({
      id: `seven_day_${model_name.toLowerCase().replace(/[^a-z0-9]+/g, '_')}`,
      kind: 'seven_day',
      used_percent,
      resets_at,
      model_scope: model_name,
    });
  let kind: BackendUsageWindowKind;
  if (value.group === 'session') kind = 'five_hour';
  else if (typeof value.group === 'string' && ['weekly', 'week', 'seven_day'].includes(value.group))
    kind = 'seven_day';
  else return null;
  return usage_window({ id: kind, kind, used_percent, resets_at, model_scope: null });
}

/**
 * The CLI passes the usage endpoint's unfiltered `limits` list through, and that list is read
 * first. Without it, `five_hour` and `seven_day` are the account windows, and each
 * `model_scoped` entry is a weekly window for the model it names: `{ display_name: 'Fable' }` is
 * `seven_day_fable`, scoped to Fable. The CLI filters `model_scoped` and leaves it out of a
 * cached answer, so it is the fallback. Windows without a usage percentage or reset time are
 * left out. Ids are unique within an observation: the first window with an id wins.
 */
function claude_usage_windows(rate_limits: Record<string, unknown>): readonly BackendUsageWindow[] {
  const windows = new Map<string, BackendUsageWindow>();
  const keep = (window: BackendUsageWindow | null) => {
    if (window !== null && !windows.has(window.id)) windows.set(window.id, window);
  };
  if (Array.isArray(rate_limits.limits))
    for (const entry of rate_limits.limits) keep(claude_limit_entry(entry));
  if (windows.size) return [...windows.values()];
  const add = (
    id: string,
    kind: BackendUsageWindowKind,
    value: unknown,
    model_scope: string | null,
  ) => {
    if (!object(value)) return;
    const used_percent = percentage(value.utilization);
    const resets_at = parse_datetime(value.resets_at);
    if (used_percent === null || resets_at === null) return;
    keep(usage_window({ id, kind, used_percent, resets_at, model_scope }));
  };
  add('five_hour', 'five_hour', rate_limits.five_hour, null);
  add('seven_day', 'seven_day', rate_limits.seven_day, null);
  if (Array.isArray(rate_limits.model_scoped))
    for (const entry of rate_limits.model_scoped) {
      if (!object(entry) || typeof entry.display_name !== 'string' || !entry.display_name) continue;
      const token = entry.display_name.toLowerCase().replace(/[^a-z0-9]+/g, '_');
      add(`seven_day_${token}`, 'seven_day', entry, entry.display_name);
    }
  return [...windows.values()];
}
