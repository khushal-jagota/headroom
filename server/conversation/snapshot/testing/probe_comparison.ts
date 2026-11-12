/** Exercise the real probe with recorded machine answers and local catalog sources. */
import type { ConversationBackendKey } from '../../contracts.ts';
import type { CodexLoginStanding, CommandOutcome } from '../contracts.ts';
import type { CodexModelCatalog, ClaudeModelCatalog } from '../provider_contracts.ts';
import {
  CodexModelCatalogUnavailable,
  ClaudeModelCatalogUnavailable,
} from '../provider_contracts.ts';
import { create_backend_probe } from '../probe.ts';
import { create_catalog_readers } from '../catalogs/readers.ts';
import { RecordingBackendProbeEnvironment } from './environment.ts';
import { parse_version, version_is_newer } from '../logic/versions.ts';

interface Case {
  readonly name: string;
  readonly backend: ConversationBackendKey;
  readonly executable: string;
  readonly real_path: string;
  readonly version: CommandOutcome;
  readonly identity: CommandOutcome | null;
  readonly writable: boolean;
  readonly latest: string | null;
  readonly codex_catalog?: CodexModelCatalog;
  readonly claude_catalog?: ClaudeModelCatalog;
  readonly hermes_catalog?: unknown;
  readonly login: {
    present: boolean;
    readable: boolean;
    account_label: string | null;
    access_token_expires_at: string | null;
    generation: { account_id: string; last_refresh: string } | null;
    rejected: boolean;
  } | null;
}
export async function run(cases: readonly Case[]) {
  const cards = [];
  for (const fixture of cases) {
    const environment = new RecordingBackendProbeEnvironment();
    const catalog_calls: unknown[] = [];
    const paths =
      fixture.backend === 'hermes' ? environment.configured_executables : environment.executables;
    paths.set(fixture.backend, fixture.executable);
    environment.real_paths.set(fixture.executable, fixture.real_path);
    if (fixture.writable) environment.writable_prefixes.add('/synthetic/.local');
    if (fixture.latest !== null)
      environment.registry_versions.set('@anthropic-ai/claude-code', fixture.latest);
    environment.outcomes.set(JSON.stringify([fixture.executable, '--version']), fixture.version);
    if (fixture.identity !== null)
      environment.outcomes.set(
        JSON.stringify([
          fixture.executable,
          ...(fixture.backend === 'claude' ? ['auth', 'status', '--json'] : ['login', 'status']),
        ]),
        fixture.identity,
      );
    const recorded_run = environment.run.bind(environment);
    environment.run = async (argv) => {
      if (!environment.outcomes.has(JSON.stringify(argv))) throw new Error('Unrecorded command');
      return recorded_run(argv);
    };
    const readers = create_catalog_readers({
      codex: async (executable) => {
        catalog_calls.push({ provider: 'codex', executable });
        if (fixture.codex_catalog === undefined) throw new CodexModelCatalogUnavailable();
        return fixture.codex_catalog;
      },
      claude: async (executable) => {
        catalog_calls.push({ provider: 'claude', executable });
        if (fixture.claude_catalog === undefined) throw new ClaudeModelCatalogUnavailable();
        return fixture.claude_catalog;
      },
      hermes: async ({ refresh }) => {
        catalog_calls.push({ provider: 'hermes', refresh });
        return {
          exit_code: 0,
          standard_output: JSON.stringify(fixture.hermes_catalog),
          standard_error: '',
        };
      },
    });
    let login: CodexLoginStanding | null = null;
    if (fixture.login !== null) {
      const value = fixture.login;
      const expires =
        value.access_token_expires_at === null ? null : new Date(value.access_token_expires_at);
      const expired = ({ now }: { now: Date }) => expires !== null && expires <= now;
      login = {
        stored: { ...value, access_token_expires_at: expires, access_token_has_expired: expired },
        generation: value.generation,
        rejected: value.rejected,
        everything_a_card_reads: (options) => [
          value.generation,
          value.rejected,
          value.present,
          value.readable,
          expired(options),
        ],
      };
    }
    const card = await create_backend_probe(readers)(fixture.backend, environment, {
      refresh: true,
      discover_update_advisory: true,
      codex_login: login,
      now: new Date('2026-11-12T12:00:00Z'),
    });
    cards.push({
      name: fixture.name,
      card,
      commands: environment.commands,
      catalog_calls,
      registry_lookups: environment.registry_lookups,
    });
  }
  return {
    cards,
    versions: ['codex-cli v1.2.3 (synthetic)', '1.2.3.4'].map(parse_version),
    ordering: [
      ['1.2.10', '1.2.9'],
      ['1.2', '1.2.0'],
      ['preview', 'beta'],
    ].map(([latest, installed]) => version_is_newer(latest, installed)),
  };
}
