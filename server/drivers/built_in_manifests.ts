/**
 * The built-in drivers' static halves: manifest, client, model rules and tool-detail reading, in
 * BUILT_IN_DRIVERS order. It loads no driver runtime, so installer-side code and SQL-level callers
 * read it.
 */
import type { DriverClientDefinition } from './core/client.ts';
import type { DriverModelRules } from './core/driver.ts';
import { default_instance_id_for_driver, parse_driver_kind } from './core/identity.ts';
import type { BackendInstanceId, DriverKind } from './core/identity.ts';
import type { DriverManifest } from './core/manifest.ts';
import { claude_client } from './claude/client.ts';
import { claude_manifest } from './claude/manifest.ts';
import { claude_models } from './claude/models.ts';
import { claude_readable_tool_call_detail } from './claude/tool_detail.ts';
import { codex_client } from './codex/client.ts';
import { codex_manifest } from './codex/manifest.ts';
import { codex_launch_model_refusal, codex_models } from './codex/models.ts';
import { hermes_client } from './hermes/client.ts';
import { hermes_manifest } from './hermes/manifest.ts';
import { hermes_models } from './hermes/models.ts';

export interface BuiltInDriverFacts {
  readonly driver_kind: DriverKind;
  readonly manifest: DriverManifest;
  readonly client: DriverClientDefinition;
  readonly models?: DriverModelRules;
  launch_model_refusal?(model: string): string | null;
  readable_tool_call_detail?(detail: string): string | null;
}
/** The same drivers and order as BUILT_IN_DRIVERS; a test holds the two equal. */
export const BUILT_IN_MANIFESTS: readonly BuiltInDriverFacts[] = [
  {
    driver_kind: parse_driver_kind('hermes'),
    manifest: hermes_manifest,
    client: hermes_client,
    models: hermes_models,
  },
  {
    driver_kind: parse_driver_kind('codex'),
    manifest: codex_manifest,
    client: codex_client,
    models: codex_models,
    launch_model_refusal: codex_launch_model_refusal,
  },
  {
    driver_kind: parse_driver_kind('claude'),
    manifest: claude_manifest,
    client: claude_client,
    models: claude_models,
    readable_tool_call_detail: claude_readable_tool_call_detail,
  },
];
/** Each built-in driver's default instance id. Card order and the unknown-backend choice list. */
export const KNOWN_BACKEND_INSTANCE_IDS: readonly BackendInstanceId[] = BUILT_IN_MANIFESTS.map(
  (driver) => default_instance_id_for_driver(driver.driver_kind),
);
/** The built-in driver behind a default instance id, or undefined. */
export function built_in_driver_of(id: string): BuiltInDriverFacts | undefined {
  return BUILT_IN_MANIFESTS.find(
    (driver) => default_instance_id_for_driver(driver.driver_kind) === id,
  );
}
