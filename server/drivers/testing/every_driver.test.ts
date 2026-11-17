/** Lists that cannot come from a driver still hold every driver. (T3 ManagedPolicy.test.ts pattern) */
import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { shipped_worker_types } from '../../worker_types/shipped.ts';
import { BUILT_IN_DRIVERS } from '../built_in.ts';
import { BUILT_IN_MANIFESTS } from '../built_in_manifests.ts';
import type { AnyAgentDriver } from '../core/driver.ts';
import { default_instance_configs } from '../core/registry.ts';

interface EveryDriverList {
  readonly name: string;
  /** True, or why the list misses the driver. */
  covers(driver: AnyAgentDriver): true | string;
}
const agent_backend_packages = Object.keys(
  (
    JSON.parse(
      readFileSync(new URL('../../../agent_backends/package.json', import.meta.url), 'utf8'),
    ) as { dependencies: Record<string, string> }
  ).dependencies,
);
const missing = (items: readonly string[], present: (item: string) => boolean): true | string => {
  const absent = items.filter((item) => !present(item));
  return absent.length ? `missing ${absent.join(', ')}` : true;
};
const EVERY_DRIVER_LISTS: readonly EveryDriverList[] = [
  {
    name: 'agent_backends/package.json installs the driver packages',
    covers: (driver) =>
      missing(driver.manifest.install?.npm_packages ?? [], (name) =>
        agent_backend_packages.includes(name),
      ),
  },
  {
    name: 'the driver package holds its icon',
    covers: (driver) =>
      missing([driver.client.icon.file], (file) =>
        existsSync(new URL(`../${driver.driver_kind}/${file}`, import.meta.url)),
      ),
  },
];

describe.each(BUILT_IN_DRIVERS.map((driver) => [driver.driver_kind, driver] as const))(
  '%s',
  (_kind, driver) => {
    it.each(EVERY_DRIVER_LISTS.map((list) => [list.name, list] as const))('%s', (_name, list) => {
      expect(list.covers(driver)).toBe(true);
    });
  },
);

it('every shipped worker type defaults to a built-in backend', () => {
  const built_in = new Set<string>(default_instance_configs(BUILT_IN_DRIVERS).keys());
  const defaults = shipped_worker_types().map(
    (raw) => (raw as { profile: { default_backend: string } }).profile.default_backend,
  );
  expect(defaults.length).toBeGreaterThan(0);
  expect(defaults.filter((backend) => !built_in.has(backend))).toEqual([]);
});

// Gap: installer-side code reads the manifest door, which must not drift from the driver list.
it('the manifest door holds the same drivers, facts and order as BUILT_IN_DRIVERS', () => {
  const facts = (driver: (typeof BUILT_IN_MANIFESTS)[number]) => [
    driver.driver_kind,
    driver.manifest,
    driver.client,
    driver.models,
    driver.launch_model_refusal,
    driver.readable_tool_call_detail,
  ];
  expect(BUILT_IN_MANIFESTS.map(facts)).toEqual(BUILT_IN_DRIVERS.map(facts));
  BUILT_IN_MANIFESTS.forEach((door, index) => {
    expect(door.manifest).toBe(BUILT_IN_DRIVERS[index]!.manifest);
    expect(door.models).toBe(BUILT_IN_DRIVERS[index]!.models);
  });
});
