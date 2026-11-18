/** Card model faces and effort controls come from the drivers' rules, as the apps drew them. */
import { expect, it } from 'vitest';
import { built_in_driver_of } from '../../drivers/built_in_manifests.ts';
import { parse_backend_instance_id } from '../../drivers/core/identity.ts';
import { model } from './catalogs/readers.ts';
import type { BackendModel } from './contracts.ts';
import { snapshot_json } from './presentation.ts';

/** The card's models as the apps read them: id, face and descriptors. */
function card_models(backend_key: string, models: BackendModel[]) {
  const card = snapshot_json(
    {
      backend_key: parse_backend_instance_id(backend_key),
      installed: true,
      executable_path: null,
      version: null,
      identity: null,
      available_models: models,
      reasoning_effort_options: ['low', 'medium', 'high'],
      default_model_id: null,
      default_reasoning_effort: 'medium',
      update_advisory: null,
      diagnoses: [],
      models_complete: true,
    },
    null,
    {
      driver: built_in_driver_of(backend_key) ?? null,
      can_sign_in: false,
      reports_allowance_usage: false,
    },
  );
  return card.available_models.map((entry) => ({
    model_id: entry.model_id,
    resolved_display_name: entry.resolved_display_name,
    option_descriptors: entry.option_descriptors,
  }));
}
const alias = (resolved_model_id: string | null, model_id = 'opus') =>
  model({ model_id, display_name: 'Opus', selected_variant: model_id, resolved_model_id });

// Gap: the readable-name and effort rules moved from both apps into the drivers. The web cases
// that covered them (model-variant-picker.test.ts) now read these fields instead.
it.each([
  ['claude', alias('claude-opus-5-5'), 'Opus 5.5'],
  ['claude', alias('claude-opus-5-5[1m]'), 'Opus 5.5 (1M)'],
  ['claude', alias('claude-haiku-4-5-20251001', 'haiku'), 'Haiku 4.5'],
  ['claude', alias('opus'), null],
  ['claude', alias(null), null],
  ['claude', alias('unrecognized-model-vNext'), null],
  [
    'codex',
    model({ model_id: 'sol', selected_variant: 'sol', resolved_model_id: 'gpt-6.1-sol' }),
    'GPT 6.1 Sol',
  ],
])('%s names %o as %s', (backend_key, entry, expected) => {
  expect(card_models(backend_key, [entry])[0]!.resolved_display_name).toBe(expected);
});

it('prefers the concrete catalog label, unless it is only the id or the variant again', () => {
  expect(
    card_models('claude', [
      alias('provider:custom'),
      model({ model_id: 'provider:custom', display_name: 'Custom model' }),
    ])[0]!.resolved_display_name,
  ).toBe('Custom model');
  for (const display_name of ['OPUS', 'claude-opus-5-5'])
    expect(
      card_models('claude', [
        alias('claude-opus-5-5'),
        model({ model_id: 'claude-opus-5-5', display_name }),
      ])[0]!.resolved_display_name,
    ).toBe('Opus 5.5');
});

it("offers a reasoning select from the model's own efforts, and none for a model without efforts", () => {
  expect(
    card_models('codex', [
      model({ model_id: 'gpt-6.1-sol', reasoning_effort_options: ['low', 'high'] }),
      model({ model_id: 'gpt-6.1-luna', reasoning_effort_options: [] }),
    ]).map((entry) => entry.option_descriptors),
  ).toEqual([
    [
      {
        type: 'select',
        id: 'reasoning_effort',
        label: 'Reasoning',
        role: 'reasoning',
        options: [
          { id: 'low', label: 'low' },
          { id: 'high', label: 'high' },
        ],
      },
    ],
    [],
  ]);
  expect(
    card_models('unlisted', [
      model({ model_id: 'anthropic/claude-opus', reasoning_effort_options: null }),
    ]),
  ).toEqual([
    { model_id: 'anthropic/claude-opus', resolved_display_name: null, option_descriptors: [] },
  ]);
});
