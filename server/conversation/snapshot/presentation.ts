/** Public cards use the shared field names and UTC timestamp text. */
import type { ProviderOptionDescriptor } from '../../../web/src/lib/usage/contracts/model.ts';
import type { BackendModel, BackendSnapshot, CardDriver } from './contracts.ts';
import type { BackendStateStore } from '../backend_state.ts';
import type { BackendUsageWindow } from '../backend_usage/contracts.ts';
import type { SignInSession } from '../../drivers/core/sign_in.ts';
export function datetime_json(value: Date): string {
  const iso = value.toISOString();
  return iso.endsWith('.000Z')
    ? iso.slice(0, -5) + 'Z'
    : iso.replace(/\.(\d{3})Z$/, (_match, fraction: string) => '.' + fraction + '000Z');
}
export function usage_window_json(window: BackendUsageWindow) {
  return {
    kind: window.kind,
    used_percent: window.used_percent,
    resets_at: datetime_json(window.resets_at),
    model_id: window.model_scope,
  };
}
/** What a card says beyond its probe. Another machine's card: its driver, and false for both. */
export interface CardFacts {
  /** The driver whose model rules name the card's models, or null. */
  readonly driver: CardDriver | null;
  readonly can_sign_in: boolean;
  readonly reports_allowance_usage: boolean;
}
/** The model's own effort list as one reasoning select. The card list is no fallback. */
function option_descriptors(model: BackendModel): ProviderOptionDescriptor[] {
  const efforts = model.reasoning_effort_options;
  if (efforts === null || efforts.length === 0) return [];
  return [
    {
      type: 'select',
      id: 'reasoning_effort',
      label: 'Reasoning',
      role: 'reasoning',
      options: efforts.map((effort) => ({ id: effort, label: effort })),
    },
  ];
}
/**
 * The name for the model an alias resolves to: the concrete catalog entry's label, unless it is
 * only the id or the variant again, else the driver's readable name. Null: nothing to add.
 */
function resolved_display_name(
  model: BackendModel,
  models: readonly BackendModel[],
  driver: CardDriver | null,
): string | null {
  const resolved = model.resolved_model_id;
  const variant = model.selected_variant;
  if (resolved === null || resolved === variant) return null;
  const label = models.find((entry) => entry.model_id === resolved)?.display_name;
  if (
    label &&
    label !== resolved &&
    (variant === null || label.toLowerCase() !== variant.toLowerCase())
  )
    return label;
  return driver?.models?.readable_name?.(resolved) ?? null;
}
export function snapshot_json(
  snapshot: BackendSnapshot,
  backend_state: BackendStateStore | null,
  facts: CardFacts,
) {
  const identity = snapshot.identity;
  const advisory = snapshot.update_advisory;
  const cached_usage = backend_state?.read_usage(snapshot.backend_key) ?? null;
  const disabled = backend_state?.read_disabled_models(snapshot.backend_key) ?? new Set<string>();
  let default_model_id = snapshot.default_model_id;
  if (
    default_model_id !== null &&
    disabled.has(default_model_id) &&
    snapshot.available_models.some((model) => model.model_id === default_model_id)
  )
    default_model_id = null;
  return {
    backend_key: snapshot.backend_key,
    installed: snapshot.installed,
    executable_path: snapshot.executable_path,
    version: snapshot.version,
    identity:
      identity === null
        ? null
        : {
            status: identity.status,
            account_label: identity.account_label,
            detail: identity.detail,
            login_command: identity.login_command,
          },
    available_models: snapshot.available_models.map((model) => ({
      model_id: model.model_id,
      display_name: model.display_name,
      selected_variant: model.selected_variant,
      version: model.version,
      resolved_model_id: model.resolved_model_id,
      detail: model.detail,
      reasoning_effort_options:
        model.reasoning_effort_options === null ? null : [...model.reasoning_effort_options],
      enabled: !disabled.has(model.model_id),
      option_descriptors: option_descriptors(model),
      resolved_display_name: resolved_display_name(model, snapshot.available_models, facts.driver),
    })),
    reasoning_effort_options: [...snapshot.reasoning_effort_options],
    default_model_id,
    default_reasoning_effort: snapshot.default_reasoning_effort,
    update_advisory:
      advisory === null
        ? null
        : {
            install_method: advisory.install_method,
            update_command:
              advisory.update_command === null ? null : advisory.update_command.join(' '),
            latest_version: advisory.latest_version,
            update_available: advisory.update_available,
            detail: advisory.detail,
          },
    diagnoses: [...snapshot.diagnoses],
    cached_usage:
      cached_usage === null
        ? null
        : {
            observed_at: datetime_json(cached_usage.observed_at),
            windows: cached_usage.windows.map(usage_window_json),
          },
    can_sign_in: facts.can_sign_in,
    reports_allowance_usage: facts.reports_allowance_usage,
  };
}
export function sign_in_json(sign_in: SignInSession) {
  return {
    sign_in: {
      session_id: sign_in.session_id,
      state: sign_in.state,
      verification_url: sign_in.verification_url,
      user_code: sign_in.user_code,
      started_at: sign_in_timestamp(sign_in.started_at),
      deadline_at: sign_in_timestamp(sign_in.deadline_at),
      detail: sign_in.detail,
      account_label: sign_in.account_label,
    },
  };
}
function sign_in_timestamp(value: Date | string): string {
  return typeof value === 'string' ? value : datetime_json(value).replace(/Z$/, '+00:00');
}
