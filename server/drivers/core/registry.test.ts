/** The registry build rules copied from T3 buildEntry, and the slug grammar they rest on. */
import { describe, expect, it } from 'vitest';
import { PlannerError } from '../../core/contracts.ts';
import type { BackendChildFactory } from '../../conversation/backends/contracts.ts';
import type { AgentDriver, AnyAgentDriver, DriverInstance } from './driver.ts';
import { NO_DRIVER_CONFIG } from './driver.ts';
import type { DriverHost } from './host.ts';
import {
  DRIVER_SLUG_MAX_CHARS,
  parse_backend_instance_id,
  parse_driver_kind,
  type BackendInstanceId,
} from './identity.ts';
import { scripted_manifest, scripted_status } from '../testing/registry.ts';
import {
  create_driver_registry,
  default_instance_configs,
  type DriverRegistry,
} from './registry.ts';
import { turn_scoped_selection_transition } from './selection.ts';

const host = { environment: {} } as unknown as DriverHost;
const factory = (() => {
  throw new Error('not started in this test');
}) as unknown as BackendChildFactory;

function scripted_driver(
  kind: string,
  overrides: Partial<AgentDriver<unknown>> = {},
  events: string[] = [],
): AnyAgentDriver {
  const driver_kind = parse_driver_kind(kind);
  return {
    driver_kind,
    home_instructions_file: 'AGENTS.md',
    client: {
      label: kind,
      icon: { file: new URL('../testing/icon.svg', import.meta.url), monochrome: true },
      accent_color: '#000000',
    },
    manifest: scripted_manifest(kind),
    config: NO_DRIVER_CONFIG,
    async create({ instance_id }): Promise<DriverInstance> {
      events.push(`create ${instance_id}`);
      await Promise.resolve();
      events.push(`created ${instance_id}`);
      return {
        instance_id,
        driver_kind,
        capabilities: {
          sessions: { supportsModelSwitchInSession: true },
          turns: { supportsActiveSteering: true },
        },
        plan_selection_transition: turn_scoped_selection_transition,
        child_factory: factory,
        status: scripted_status(kind),
        dispose: async () => {
          events.push(`dispose ${instance_id}`);
        },
      };
    },
    ...overrides,
  };
}
function build(
  drivers: readonly AnyAgentDriver[],
  configs = default_instance_configs(drivers),
): Promise<DriverRegistry> {
  return create_driver_registry({ drivers, configs, host, signal: new AbortController().signal });
}
function refusal(run: () => unknown): PlannerError {
  try {
    run();
  } catch (error) {
    expect(error).toBeInstanceOf(PlannerError);
    return error as PlannerError;
  }
  throw new Error('expected a refusal');
}

describe('slug grammar', () => {
  it.each(['codex', 'a', 'Claude_2-x', 'a'.repeat(DRIVER_SLUG_MAX_CHARS)])('accepts %s', (slug) => {
    expect(parse_driver_kind(slug)).toBe(slug);
    expect(parse_backend_instance_id(slug)).toBe(slug);
  });
  it.each([
    '',
    '2codex',
    '-codex',
    '_codex',
    'co dex',
    ' codex',
    'co.dex',
    'a'.repeat(65),
    null,
    7,
  ])('refuses %j', (value) => {
    expect(refusal(() => parse_driver_kind(value)).code).toBe('validation');
    expect(refusal(() => parse_backend_instance_id(value)).code).toBe('validation');
  });
});

describe('registry build', () => {
  it('builds one default instance per driver, one after another, in list order', async () => {
    const events: string[] = [];
    const drivers = [
      scripted_driver('hermes', {}, events),
      scripted_driver('codex', {}, events),
      scripted_driver('optional', { has_default_instance: false }, events),
      scripted_driver('claude', {}, events),
    ];
    expect([...default_instance_configs(drivers)]).toEqual([
      ['hermes', { driver: 'hermes' }],
      ['codex', { driver: 'codex' }],
      ['claude', { driver: 'claude' }],
    ]);
    const registry = await build(drivers);
    expect(events).toEqual([
      'create hermes',
      'created hermes',
      'create codex',
      'created codex',
      'create claude',
      'created claude',
    ]);
    expect(
      registry.list().map((entry) => entry.kind === 'live' && entry.instance.instance_id),
    ).toEqual(['hermes', 'codex', 'claude']);
    expect([...registry.drivers.keys()]).toEqual(['hermes', 'codex', 'optional', 'claude']);
    expect(registry.require_live('codex').child_factory).toBe(factory);
  });

  it('keeps an envelope for an unregistered driver as an unavailable entry', async () => {
    const id = parse_backend_instance_id('elsewhere');
    const registry = await build(
      [scripted_driver('codex')],
      new Map([[id, { driver: parse_driver_kind('gone'), config: { kept: true } }]]),
    );
    expect(registry.get('elsewhere')).toEqual({
      kind: 'unavailable',
      instance_id: 'elsewhere',
      driver: null,
      reason: "Driver 'gone' is not registered in this build.",
    });
  });

  it('decodes stored config, or the defaults when none is stored, and refuses a bad one', async () => {
    const decoded: unknown[] = [];
    const driver = scripted_driver('codex', {
      config: {
        defaults: () => ({ model: 'default' }),
        parse(raw) {
          decoded.push(raw);
          if ((raw as { model?: unknown }).model === 7) throw new Error('model must be text');
          return raw;
        },
      },
    });
    const ok = parse_backend_instance_id('codex');
    const bad = parse_backend_instance_id('codex_bad');
    const registry = await build(
      [driver],
      new Map<BackendInstanceId, { driver: typeof driver.driver_kind; config?: unknown }>([
        [ok, { driver: driver.driver_kind }],
        [bad, { driver: driver.driver_kind, config: { model: 7 } }],
      ]),
    );
    expect(decoded).toEqual([{ model: 'default' }, { model: 7 }]);
    expect(registry.get('codex')?.kind).toBe('live');
    expect(registry.get('codex_bad')).toMatchObject({
      kind: 'unavailable',
      driver,
      reason: "Invalid config for backend 'codex_bad': model must be text",
      cause: new Error('model must be text'),
    });
  });

  it('turns a failed create into an unavailable entry and keeps building', async () => {
    const failure = new TypeError('the Claude SDK is not installed');
    const registry = await build([
      scripted_driver('claude', {
        create: async () => {
          throw failure;
        },
      }),
      scripted_driver('codex'),
    ]);
    expect(registry.get('claude')).toMatchObject({
      kind: 'unavailable',
      instance_id: 'claude',
      reason: "Backend 'claude' could not start: the Claude SDK is not installed",
      cause: failure,
    });
    expect(registry.get('codex')?.kind).toBe('live');
  });

  it('refuses a missing or unavailable id with the unknown backend error and no fallback', async () => {
    const registry = await build([
      scripted_driver('hermes', {
        create: async () => {
          throw new Error('no python');
        },
      }),
      scripted_driver('codex'),
    ]);
    expect(registry.get('nothing')).toBeUndefined();
    const missing = refusal(() => registry.require_live('nothing'));
    expect([missing.code, missing.message, missing.detail]).toEqual([
      'validation',
      'unknown agent backend',
      { backend_key: 'nothing', backend_keys: ['codex'] },
    ]);
    expect(refusal(() => registry.require_live('hermes')).detail).toEqual({
      backend_key: 'hermes',
      backend_keys: ['codex'],
      reason: "Backend 'hermes' could not start: no python",
    });
  });

  it('disposes every live instance, then reports each failure', async () => {
    const events: string[] = [];
    const failing = scripted_driver('codex', {}, events);
    const registry = await build([
      scripted_driver('hermes', {}, events),
      {
        ...failing,
        async create(input, driver_host, signal) {
          const instance = await failing.create(input, driver_host, signal);
          return {
            ...instance,
            // A synchronous throw must not keep the later instance from disposing.
            dispose: () => {
              events.push('dispose codex');
              throw new Error('codex would not stop');
            },
          };
        },
      },
      scripted_driver('claude', {}, events),
    ]);
    events.length = 0;
    await expect(registry.dispose()).rejects.toMatchObject({
      errors: [new Error('codex would not stop')],
    });
    expect(events).toEqual(['dispose hermes', 'dispose codex', 'dispose claude']);
  });

  // Gap: Codex's cleanup waits on Claude's catalog retry, so a serial dispose would hang.
  it('disposes every instance at once, so one dispose can wait on another', async () => {
    const claude_disposed = Promise.withResolvers<void>();
    const with_dispose = (kind: string, dispose: () => Promise<void>): AnyAgentDriver => {
      const driver = scripted_driver(kind);
      return {
        ...driver,
        async create(input, driver_host, signal) {
          return { ...(await driver.create(input, driver_host, signal)), dispose };
        },
      };
    };
    const options = { deadline: 1 } as never;
    const seen: unknown[] = [];
    const registry = await build([
      with_dispose('codex', async () => {
        await claude_disposed.promise;
      }),
      with_dispose('claude', async (...args: unknown[]) => {
        seen.push(args[0]);
        claude_disposed.resolve();
      }),
    ]);
    await registry.dispose(options);
    expect(seen).toEqual([options]);
  });

  it('takes a fourth driver from the list alone', async () => {
    const drivers = ['hermes', 'codex', 'claude', 'scripted'].map((kind) => scripted_driver(kind));
    const registry = await build(drivers);
    expect(registry.list().map((entry) => entry.kind)).toEqual(['live', 'live', 'live', 'live']);
    expect(registry.require_live('scripted').driver_kind).toBe('scripted');
  });
});
