/** Antigravity's card reader: the models and thinking levels a throwaway agy session offers. */
import type {
  SessionConfigOption,
  SessionConfigSelectOption,
} from '@agentclientprotocol/sdk/unstable';
import type { BackendModel, CatalogReader } from '../../../conversation/snapshot/contracts.ts';
import { answer, model } from '../../../conversation/snapshot/catalogs/readers.ts';
import { identity } from '../../../conversation/snapshot/identity.ts';
import { BackendCliIncompatible } from '../../../conversation/backends/cli_compatibility.ts';
import type { AntigravityCatalogSource } from './contracts.ts';

type SelectOption = Extract<SessionConfigOption, { type: 'select' }>;
export interface AntigravityCatalog {
  readonly models: readonly BackendModel[];
  /** Every level any model offers, in the order agy first lists them. */
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string;
  readonly default_reasoning_effort: string | null;
  /** False when some model's levels could not be read. */
  readonly complete: boolean;
}
export function select_option(
  options: readonly SessionConfigOption[],
  category: 'model' | 'thought_level',
): SelectOption | undefined {
  return options.find(
    (option): option is SelectOption => option.type === 'select' && option.category === category,
  );
}
/** A select's values, with any groups flattened. */
export function select_values(option: SelectOption): readonly SessionConfigSelectOption[] {
  return option.options.flatMap((entry) => ('value' in entry ? [entry] : entry.options));
}
function levels(options: readonly SessionConfigOption[]): readonly string[] | null {
  const thought = select_option(options, 'thought_level');
  const values = thought === undefined ? [] : select_values(thought).map((entry) => entry.value);
  return values.length ? values : null;
}
/** Null: the session offers no model choice. */
export function antigravity_catalog(
  initial: readonly SessionConfigOption[],
  after_selecting: ReadonlyMap<string, readonly SessionConfigOption[]>,
): AntigravityCatalog | null {
  const model_option = select_option(initial, 'model');
  if (model_option === undefined) return null;
  const efforts: string[] = [];
  let complete = true;
  const models = select_values(model_option).map((entry) => {
    const options =
      entry.value === model_option.currentValue ? initial : after_selecting.get(entry.value);
    if (options === undefined) complete = false;
    const offered = options === undefined ? null : levels(options);
    for (const level of offered ?? []) if (!efforts.includes(level)) efforts.push(level);
    return model({
      model_id: entry.value,
      display_name: entry.name,
      reasoning_effort_options: offered,
    });
  });
  return {
    models,
    reasoning_effort_options: efforts,
    default_model_id: model_option.currentValue,
    default_reasoning_effort: select_option(initial, 'thought_level')?.currentValue ?? null,
    complete,
  };
}
export function antigravity_catalog_reader(source: AntigravityCatalogSource | null): CatalogReader {
  return async (request) => {
    let read;
    try {
      if (source === null) throw new Error('this instance reads no catalog');
      read = await source(request.executable_path);
    } catch (error) {
      return answer({
        identity: identity('unknown'),
        diagnoses: [
          error instanceof BackendCliIncompatible
            ? error.message
            : 'Antigravity is installed but did not answer when asked what it can run, so no models are listed. Check that `agy acp` starts from a terminal.',
        ],
      });
    }
    // agy has no identity command: a session opening is the sign-in answer.
    if (!read.signed_in) return answer({ identity: identity('unauthenticated') });
    const signed_in = identity('authenticated');
    const catalog = antigravity_catalog(read.initial, read.after_selecting);
    if (catalog === null)
      return answer({
        identity: signed_in,
        diagnoses: ['Antigravity offered no model choice, so no models are listed.'],
      });
    return answer({ ...catalog, identity: signed_in });
  };
}
