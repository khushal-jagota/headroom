/** Child-scoped, independent catalogue sources and bounded recovery. */
import { createHash } from 'node:crypto';
import { casefold } from '../../../core/casefold.ts';
import { ComposerCatalogEntry, ComposerCatalogEntryKind } from '../../contracts.ts';
import * as protocol from './protocol.ts';
import { CodexRequestRejected, CodexWireFailed } from './errors.ts';
import {
  APP_HINT_COOLDOWN_SECONDS,
  APP_METADATA_BATCH_SIZE,
  APP_METADATA_TTL_SECONDS,
  CATALOG_DIAGNOSTIC_MAXIMUM_CHARACTERS,
  CATALOG_REFRESH_COALESCE_SECONDS,
  CATALOG_RETRY_INITIAL_SECONDS,
  CATALOG_RETRY_MAXIMUM_SECONDS,
} from './catalog_config.ts';

type LaneName = 'skills' | 'plugins' | 'installed' | 'metadata';
interface Lane {
  pending: boolean;
  failures: number;
  deadline: number;
  wake: (() => void) | null;
  task: Promise<void> | null;
}
interface CatalogReads {
  read_skills(): Promise<protocol.SkillsListResponse>;
  read_plugins(): Promise<protocol.PluginInstalledResponse>;
  read_installed(): Promise<protocol.AppsInstalledResponse>;
  read_metadata(ids: string[]): Promise<protocol.AppsReadResponse>;
  publish(): Promise<void>;
  clock?: () => number;
}
const LANE_NAMES: readonly LaneName[] = ['skills', 'plugins', 'installed', 'metadata'];
const CLOSED = Symbol('catalog closed');

export class CatalogCoordinator {
  skills: protocol.SkillsListResponse | null = null;
  plugins: protocol.PluginInstalledResponse | null = null;
  installed: protocol.AppsInstalledResponse | null = null;
  metadata = new Map<string, readonly [number, protocol.ConnectorMetadata | null]>();
  closed = false;
  private readonly reads: CatalogReads;
  private readonly clock: () => number;
  private readonly lanes = new Map<LaneName, Lane>();
  private identities = new Set<string>();
  private generation = 0;
  private readonly closure: Promise<typeof CLOSED>;
  private resolve_closure!: (value: typeof CLOSED) => void;

  constructor(options: CatalogReads) {
    this.reads = options;
    this.clock = options.clock ?? (() => performance.now() / 1000);
    this.closure = new Promise((resolve) => {
      this.resolve_closure = resolve;
    });
    for (const name of LANE_NAMES)
      this.lanes.set(name, { pending: false, failures: 0, deadline: 0, wake: null, task: null });
  }

  start(): void {
    if (this.closed) return;
    for (const name of LANE_NAMES) {
      const lane = this.lanes.get(name)!;
      lane.task ??= this.run(name, lane);
    }
    for (const name of ['skills', 'plugins', 'installed'] as const) this.request(name, false);
  }

  private request(name: LaneName, coalesce = true): void {
    if (this.closed) return;
    const lane = this.lanes.get(name)!;
    if (!lane.pending && coalesce)
      lane.deadline = Math.max(lane.deadline, this.clock() + CATALOG_REFRESH_COALESCE_SECONDS);
    lane.pending = true;
    lane.wake?.();
  }

  hint_apps(): void {
    this.request('installed');
  }

  hint_skills(): void {
    this.request('skills');
    this.request('plugins');
  }

  wake(): void {
    for (const lane of this.lanes.values()) lane.wake?.();
  }

  async close(): Promise<void> {
    this.closed = true;
    this.resolve_closure(CLOSED);
    this.wake();
    await Promise.all([...this.lanes.values()].map((lane) => lane.task));
  }

  private async wait(lane: Lane, delay: number | null): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await new Promise<void>((resolve) => {
        lane.wake = resolve;
        if (delay !== null) timer = setTimeout(resolve, delay * 1000);
      });
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      lane.wake = null;
    }
  }

  private retry(name: LaneName, lane: Lane, failure: unknown): void {
    lane.failures += 1;
    const delay = Math.min(
      CATALOG_RETRY_MAXIMUM_SECONDS,
      CATALOG_RETRY_INITIAL_SECONDS * 2 ** Math.min(lane.failures - 1, 16),
    );
    lane.deadline = this.clock() + delay;
    lane.pending = true;
    log_failure(name, failure, delay);
  }

  private async run(name: LaneName, lane: Lane): Promise<void> {
    while (!this.closed) {
      const delay = lane.deadline - this.clock();
      if (!lane.pending || delay > 0) {
        await this.wait(lane, lane.pending ? delay : null);
        continue;
      }
      lane.pending = false;
      let read_succeeded = false;
      try {
        const read =
          name === 'skills'
            ? this.reads.read_skills()
            : name === 'plugins'
              ? this.reads.read_plugins()
              : name === 'installed'
                ? this.reads.read_installed()
                : this.refresh_metadata();
        const result = await Promise.race([read, this.closure]);
        if (this.closed || result === CLOSED) return;
        if (name === 'skills') this.skills = result as protocol.SkillsListResponse;
        else if (name === 'plugins') this.plugins = result as protocol.PluginInstalledResponse;
        else if (name === 'installed') this.set_installed(result as protocol.AppsInstalledResponse);
        read_succeeded = true;
        lane.deadline = name === 'installed' ? this.clock() + APP_HINT_COOLDOWN_SECONDS : 0;
      } catch (failure) {
        if (this.closed) return;
        if (name === 'skills') this.skills = null;
        else if (name === 'plugins') this.plugins = null;
        else if (name === 'installed') this.set_installed(null);
        this.retry(name, lane, failure);
      }
      try {
        await Promise.race([this.reads.publish(), this.closure]);
        if (this.closed) return;
        if (read_succeeded) lane.failures = 0;
      } catch (failure) {
        if (this.closed) return;
        if (read_succeeded) this.retry(name, lane, failure);
      }
    }
  }

  private set_installed(installed: protocol.AppsInstalledResponse | null): void {
    this.installed = installed;
    const identities = new Set(
      (installed?.apps ?? []).filter((app) => app.enabled && app.callable).map((app) => app.id),
    );
    if (
      identities.size !== this.identities.size ||
      [...identities].some((identity) => !this.identities.has(identity))
    ) {
      this.generation += 1;
      this.identities = identities;
    }
    this.metadata = new Map(
      [...this.metadata].filter(
        ([key, value]) => identities.has(key) && this.clock() - value[0] < APP_METADATA_TTL_SECONDS,
      ),
    );
    if (identities.size) this.request('metadata', false);
  }

  private async refresh_metadata(): Promise<void> {
    const generation = this.generation;
    const now = this.clock();
    const missing = [...this.identities]
      .filter((key) => !this.metadata.has(key) || now - this.metadata.get(key)![0] >= APP_METADATA_TTL_SECONDS)
      .sort(compare_text);
    for (let offset = 0; offset < missing.length; offset += APP_METADATA_BATCH_SIZE) {
      const ids = missing.slice(offset, offset + APP_METADATA_BATCH_SIZE);
      const response = await Promise.race([this.reads.read_metadata(ids), this.closure]);
      if (this.closed || response === CLOSED || generation !== this.generation) return;
      const returned = new Map(response.apps.map((app) => [app.id, app]));
      for (const app_id of ids) this.metadata.set(app_id, [this.clock(), returned.get(app_id) ?? null]);
    }
  }
}

function log_failure(source: string, failure: unknown, delay: number): void {
  let category = 'request';
  let code = '';
  if (failure instanceof CodexRequestRejected) {
    category = 'rpc';
    if (Number.isInteger(failure.code)) code = ` code=${failure.code}`;
  } else if (failure instanceof protocol.CodexProtocolError) category = 'validation';
  else if (failure instanceof CodexWireFailed) category = 'wire';
  else if (failure instanceof Error && failure.name === 'TimeoutError') category = 'timeout';
  console.warn(
    `Codex catalogue ${source} failed kind=${category}${code} retry=${delay}s`.slice(
      0,
      CATALOG_DIAGNOSTIC_MAXIMUM_CHARACTERS,
    ),
  );
}

export interface CatalogInvocation {
  readonly kind: ComposerCatalogEntryKind;
  readonly name: string;
  readonly path: string | null;
}
export class CatalogSnapshot {
  readonly entries: readonly ComposerCatalogEntry[];
  private readonly invocations: ReadonlyMap<string, CatalogInvocation | null>;
  constructor(
    entries: readonly ComposerCatalogEntry[] = [],
    invocations: ReadonlyMap<string, CatalogInvocation | null> = new Map(),
  ) {
    this.entries = entries;
    this.invocations = invocations;
  }
  resolve(token: string): CatalogInvocation | null {
    return this.invocations.get(token) ?? null;
  }
}
export const EMPTY_CATALOG_SNAPSHOT = new CatalogSnapshot();
export const CODEX_BUILT_IN_CATALOG_ENTRIES: readonly ComposerCatalogEntry[] = [
  new ComposerCatalogEntry({ kind: 'command', display_text: '/compact', insertion_text: '/compact', description: 'Compact the conversation context' }),
  new ComposerCatalogEntry({ kind: 'command', display_text: '/review', insertion_text: '/review ', description: 'Review the working tree or follow the supplied review instructions', argument_hint: '[review instructions]' }),
  new ComposerCatalogEntry({ kind: 'command', display_text: '/goal', insertion_text: '/goal ', description: 'Inspect, set, or clear the Codex thread goal', argument_hint: '[get | set <objective> | clear]' }),
];

type Candidate = readonly [string, ComposerCatalogEntry, CatalogInvocation];
function compare_text(a: string, b: string): number {
  // Python orders Unicode code points, without locale-specific collation.
  const left = Array.from(a, (character) => character.codePointAt(0)!);
  const right = Array.from(b, (character) => character.codePointAt(0)!);
  for (let index = 0; index < Math.min(left.length, right.length); index += 1)
    if (left[index] !== right[index]) return left[index]! - right[index]!;
  return left.length - right.length;
}
function compare_tuple(a: readonly string[], b: readonly string[]): number {
  for (let index = 0; index < a.length; index += 1) {
    const compared = compare_text(a[index]!, b[index]!);
    if (compared) return compared;
  }
  return 0;
}
function mention_token(name: string): string {
  return casefold(name).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'item';
}
function collision_alias(token: string, invocation: CatalogInvocation, allocated: Set<string>): string {
  const identity = `${invocation.kind}\0${invocation.name}\0${invocation.path ?? ''}`;
  const digest = createHash('sha256').update(identity, 'utf8').digest('hex');
  for (let length = 8; length <= digest.length; length += 1) {
    const alias = `${token}~${digest.slice(0, length)}`;
    if (!allocated.has(alias)) return alias;
  }
  let serial = 2;
  while (allocated.has(`${token}~${digest}-${serial}`)) serial += 1;
  return `${token}~${digest}-${serial}`;
}

export function catalog_snapshot(
  skills: protocol.SkillsListResponse | null,
  installed_apps: protocol.AppsInstalledResponse | null,
  apps: readonly protocol.ConnectorMetadata[] | null,
  plugins: protocol.PluginInstalledResponse | null,
): CatalogSnapshot {
  const candidates: Candidate[] = [];
  for (const cwd of skills?.data ?? []) {
    for (const skill of cwd.skills) {
      if (!skill.enabled) continue;
      const token = `$${skill.name}`;
      const display_name = skill.interface?.displayName || skill.name;
      const description = skill.interface?.shortDescription || skill.shortDescription || skill.description;
      candidates.push([token, new ComposerCatalogEntry({ kind: 'skill', display_text: `$${display_name}`, insertion_text: `${token} `, description }), { kind: 'skill', name: skill.name, path: skill.path }]);
    }
  }
  const metadata = new Map((apps ?? []).map((app) => [app.id, app]));
  for (const runtime_app of installed_apps?.apps ?? []) {
    if (!runtime_app.enabled || !runtime_app.callable) continue;
    const app = metadata.get(runtime_app.id);
    const name = (app?.name.trim() ? app.name : null) || (runtime_app.runtimeName?.trim() ? runtime_app.runtimeName : runtime_app.id);
    const token = `@${mention_token(name)}`;
    candidates.push([token, new ComposerCatalogEntry({ kind: 'app', display_text: `@${name}`, insertion_text: `${token} `, description: app?.description || `Use ${name}` }), { kind: 'app', name, path: `app://${runtime_app.id}` }]);
  }
  for (const marketplace of plugins?.marketplaces ?? []) {
    for (const plugin of marketplace.plugins) {
      if (!plugin.installed || !plugin.enabled || plugin.availability === 'DISABLED_BY_ADMIN' || plugin.disabledReason !== null) continue;
      const display_name = plugin.interface?.displayName || plugin.name;
      const token = `@${mention_token(plugin.name)}@${mention_token(marketplace.name)}`;
      candidates.push([token, new ComposerCatalogEntry({ kind: 'plugin', display_text: `@${display_name}`, insertion_text: `${token} `, description: plugin.interface?.shortDescription || `Use ${display_name}` }), { kind: 'plugin', name: display_name, path: `plugin://${plugin.name}@${marketplace.name}` }]);
    }
  }
  const deduplicated = new Map<string, Candidate>();
  for (const candidate of candidates) {
    const invocation = candidate[2];
    const identity = JSON.stringify([invocation.kind, invocation.name, invocation.path ?? '']);
    if (!deduplicated.has(identity)) deduplicated.set(identity, candidate);
  }
  const by_token = new Map<string, Candidate[]>();
  for (const candidate of deduplicated.values()) {
    const colliding = by_token.get(candidate[0]) ?? [];
    colliding.push(candidate);
    by_token.set(candidate[0], colliding);
  }
  const executable: Candidate[] = [];
  const allocated = new Set(by_token.keys());
  for (const token of [...by_token.keys()].sort((a, b) => compare_tuple([casefold(a), a], [casefold(b), b]))) {
    const colliding = by_token.get(token)!;
    if (colliding.length === 1) {
      executable.push(colliding[0]!);
      continue;
    }
    colliding.sort((a, b) => compare_tuple([a[2].kind, a[2].name, a[2].path ?? ''], [b[2].kind, b[2].name, b[2].path ?? '']));
    for (const [, entry, invocation] of colliding) {
      const alias = collision_alias(token, invocation, allocated);
      allocated.add(alias);
      executable.push([alias, new ComposerCatalogEntry({ ...entry, display_text: alias, insertion_text: `${alias} ` }), invocation]);
    }
  }
  executable.sort((a, b) => compare_tuple([a[1].kind, casefold(a[0])], [b[1].kind, casefold(b[0])]));
  const invocations = new Map<string, CatalogInvocation | null>([
    ['/compact', { kind: 'command', name: 'compact', path: null }],
    ['/review', { kind: 'command', name: 'review', path: null }],
    ['/goal', { kind: 'command', name: 'goal_get', path: null }],
  ]);
  for (const [token, , invocation] of executable) invocations.set(token, invocation);
  return new CatalogSnapshot([...CODEX_BUILT_IN_CATALOG_ENTRIES, ...executable.map((entry) => entry[1])], invocations);
}
