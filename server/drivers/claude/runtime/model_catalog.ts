/** Model choices come from the selected CLI's startup handshake, without a prompt. */
import { is_record as object } from '../../../core/records.ts';
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { OneShotChildren } from '../../../lifecycle/one_shot_children.ts';
import { run_claude_probe_session } from './probe_session.ts';
import type { ClaudeProbeSessionOptions } from './probe_session.ts';

export const MODEL_CATALOG_TIMEOUT_SECONDS = 30;
export class ClaudeModelCatalogUnavailable extends Error {}
export interface ClaudeModel {
  readonly model_id: string;
  readonly display_name: string;
  readonly resolved_model_id: string;
  readonly reasoning_effort_options: readonly string[];
}
export interface ClaudeModelCatalog {
  readonly models: readonly ClaudeModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
}

export function versioned_display_name(resolved_model_id: string): string {
  const large_context = resolved_model_id.endsWith('[1m]');
  const identifier = large_context ? resolved_model_id.slice(0, -4) : resolved_model_id;
  const parts = identifier.split('-');
  if (parts[0] !== 'claude' || parts.length < 2) return resolved_model_id;
  const raw_family = parts[1]!;
  const family = raw_family.charAt(0).toUpperCase() + raw_family.slice(1).toLowerCase();
  const version_parts = parts.slice(2).filter((part) => !/^\d{8}$/.test(part));
  if (!version_parts.every((part) => /^\d+$/.test(part))) return resolved_model_id;
  const shown = version_parts.length === 0 ? family : `${family} ${version_parts.join('.')}`;
  return large_context ? `${shown} (1M)` : shown;
}

export function claude_model_catalog_from_init(info: unknown): ClaudeModelCatalog {
  if (!object(info) || !Array.isArray(info.models)) {
    throw new ClaudeModelCatalogUnavailable(
      "this claude's startup handshake does not report models",
    );
  }
  const models: ClaudeModel[] = [];
  const efforts_in_listed_order: string[] = [];
  let default_resolves_to: string | null = null;
  for (const entry of info.models) {
    if (!object(entry)) continue;
    const value = entry.value;
    const resolved = entry.resolvedModel;
    if (typeof value !== 'string' || typeof resolved !== 'string') continue;
    if (value === 'default') {
      default_resolves_to = resolved;
      continue;
    }
    const raw_efforts = entry.supportedEffortLevels;
    const efforts = Array.isArray(raw_efforts)
      ? raw_efforts.filter((effort): effort is string => typeof effort === 'string')
      : [];
    for (const effort of efforts) {
      if (!efforts_in_listed_order.includes(effort)) efforts_in_listed_order.push(effort);
    }
    if (models.some((model) => model.model_id === resolved)) continue;
    models.push({
      model_id: resolved,
      display_name:
        typeof entry.displayName === 'string' && entry.displayName
          ? entry.displayName
          : versioned_display_name(resolved),
      resolved_model_id: resolved,
      reasoning_effort_options: efforts,
    });
  }
  if (models.length === 0)
    throw new ClaudeModelCatalogUnavailable("claude's startup handshake named no models");
  return {
    models,
    reasoning_effort_options: efforts_in_listed_order,
    default_model_id:
      models.find((model) => model.resolved_model_id === default_resolves_to)?.model_id ?? null,
  };
}

export async function probe_claude_model_catalog(
  claude_executable: string,
  children: OwnedOneShotChildren = new OneShotChildren(),
  options: ClaudeProbeSessionOptions = {},
): Promise<ClaudeModelCatalog> {
  const info = await run_claude_probe_session(claude_executable, children, options, {
    timeout_seconds: MODEL_CATALOG_TIMEOUT_SECONDS,
    timed_out_message: 'claude did not finish its startup handshake in time',
    unavailable: ClaudeModelCatalogUnavailable,
    read: (cli): Promise<unknown> => cli.initializationResult(),
  });
  // Startup failures are wrapped by the session. Catalog transformation errors keep their own type.
  return claude_model_catalog_from_init(info);
}
