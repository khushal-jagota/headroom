import { readFileSync } from 'node:fs';
import type { NewSessionResponse, SessionConfigOption } from '@agentclientprotocol/sdk/unstable';
import { expect, test } from 'vitest';
import { RecordingBackendProbeEnvironment } from '../../../conversation/snapshot/recording_environment.ts';
import type { AntigravityCatalogRead } from './contracts.ts';
import { create_antigravity_status } from './status.ts';
import { antigravity_catalog } from './status_readers.ts';

/** agy 1.5.10's own `session/new` result, recorded on the VPS. */
const recorded = JSON.parse(
  readFileSync(new URL('./testing/agy_1.5.10_session_new.json', import.meta.url), 'utf8'),
) as NewSessionResponse;
const initial = recorded.configOptions!;
/** The config options with the thinking levels agy reports once a model is selected. */
const selecting = (levels: readonly string[] | null): readonly SessionConfigOption[] =>
  initial.flatMap((option) => {
    if (option.category !== 'thought_level') return [option];
    if (levels === null) return [];
    return [{ ...option, options: levels.map((value) => ({ value, name: value })) }];
  });

// Risk: the card offers a model a level agy refuses for it, or hides one it accepts. Levels come
// per model from the option agy reports after selecting it; Hydrogen alone has xhigh (verified
// live on 1.5.10), GPT-OSS has no thinking option at all.
test('each model offers the thinking levels agy reports for it, and the card defaults are agy’s', () => {
  const three = ['low', 'medium', 'high'];
  const levels_after_selecting: Record<string, readonly string[] | null> = {
    'gemini-4-hydrogen': [...three, 'xhigh'],
    'gemini-pro-orchestrator-g1-pro': three,
    'gemini-3.8-flash': three,
    'claude-opus-5-5': three,
    'claude-sonnet-5-5': three,
    'gpt-oss-120b-medium': null,
  };
  const after_selecting = new Map(
    Object.entries(levels_after_selecting).map(([model_id, levels]) => [
      model_id,
      selecting(levels),
    ]),
  );
  const catalog = antigravity_catalog(initial, after_selecting)!;
  expect(
    catalog.models.map((model) => [
      model.model_id,
      model.display_name,
      model.reasoning_effort_options,
    ]),
  ).toEqual([
    ['gemini-4-argon', 'Gemini 4 Argon', ['low', 'medium', 'high']],
    ['gemini-4-hydrogen', 'Gemini 4 Hydrogen', ['low', 'medium', 'high', 'xhigh']],
    ['gemini-pro-orchestrator-g1-pro', 'Orchestrator (Argon)', ['low', 'medium', 'high']],
    ['gemini-3.8-flash', 'Gemini 3.8 Flash', ['low', 'medium', 'high']],
    ['claude-opus-5-5', 'Claude Opus 5.5', ['low', 'medium', 'high']],
    ['claude-sonnet-5-5', 'Claude Sonnet 5.5', ['low', 'medium', 'high']],
    ['gpt-oss-120b-medium', 'GPT-OSS 120B (Medium)', null],
  ]);
  expect(catalog.reasoning_effort_options).toEqual(['low', 'medium', 'high', 'xhigh']);
  expect(catalog.default_model_id).toBe('gemini-4-argon');
  expect(catalog.default_reasoning_effort).toBe('medium');
  expect(catalog.complete).toBe(true);
  // A model whose selection agy refused has unknown levels, so the list is not complete.
  after_selecting.delete('claude-opus-5-5');
  const partial = antigravity_catalog(initial, after_selecting)!;
  expect(partial.models[4]!.reasoning_effort_options).toBeNull();
  expect(partial.complete).toBe(false);
});

// Risk: agy has no identity command, so a card without a catalog-derived identity never offers
// the sign-in the web gates on `identity.status === 'unauthenticated'`.
test('the card’s sign-in state comes from whether agy opened a catalog session', async () => {
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('agy', '/synthetic/agy');
  const card_identity = async (read: () => Promise<AntigravityCatalogRead>) => {
    const card = await create_antigravity_status({ catalog: read }).probe({
      environment,
      refresh: true,
      discover_update_advisory: false,
      now: new Date(0),
      model_version: () => [],
    });
    return card.identity?.status;
  };
  expect(await card_identity(async () => ({ signed_in: false }))).toBe('unauthenticated');
  expect(
    await card_identity(async () => ({ signed_in: true, initial, after_selecting: new Map() })),
  ).toBe('authenticated');
  expect(
    await card_identity(async () => {
      throw new Error('agy did not answer');
    }),
  ).toBe('unknown');
});
