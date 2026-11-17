/** Lists that cannot come from a driver still hold every driver. (T3 ManagedPolicy.test.ts pattern) */
import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { shipped_worker_types } from '../../worker_types/shipped.ts';
import { BUILT_IN_DRIVERS } from '../built_in.ts';
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
/** Every CHECK expression in the release seed, parentheses balanced, quoted text skipped. */
const seed_checks = (
  JSON.parse(
    readFileSync(new URL('../../core/database/application_schema.json', import.meta.url), 'utf8'),
  ) as { statements: string[] }
).statements.flatMap((statement) =>
  [...statement.matchAll(/\bCHECK\s*\(/gi)].map((match) => {
    let depth = 0,
      quoted = false,
      end = match.index + match[0].length - 1;
    for (; end < statement.length; end += 1) {
      const character = statement[end];
      if (character === "'") quoted = !quoted;
      else if (!quoted && character === '(') depth += 1;
      else if (!quoted && character === ')' && --depth === 0) break;
    }
    return statement.slice(match.index, end + 1);
  }),
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
    // Backend keys are open strings since schema step ts_0001_open_backend_keys.
    name: 'application_schema.json names the driver kind in no CHECK',
    covers: (driver) => {
      const closed = seed_checks.filter((check) => check.includes(`'${driver.driver_kind}'`));
      return closed.length ? `closed by ${closed.join('; ')}` : true;
    },
  },
  {
    // GET /api/drivers takes the media type from the extension and throws on any other.
    name: 'the driver package holds its icon, an SVG or a PNG',
    covers: (driver) => {
      const file = driver.client.icon.file;
      if (!existsSync(file)) return `missing ${file.pathname}`;
      return /\.(?:svg|png)$/.test(file.pathname) || `${file.pathname} is neither SVG nor PNG`;
    },
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
