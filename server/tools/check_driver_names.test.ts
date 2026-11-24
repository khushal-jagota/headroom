/** The driver-name lint's own rules, over in-memory files. */
import { describe, expect, it } from 'vitest';
import type { AnyAgentDriver } from '../drivers/core/driver.ts';
import { parse_driver_kind } from '../drivers/core/identity.ts';
import { scripted_manifest } from '../drivers/testing/registry.ts';
import { check_driver_names, name_parts } from './check_driver_names.ts';
import type {
  DriverImportAllowance,
  DriverNameAllowance,
  SourceFile,
} from './check_driver_names.ts';

const driver = (
  kind: string,
  manifest: Partial<AnyAgentDriver['manifest']> = {},
): Pick<AnyAgentDriver, 'driver_kind' | 'manifest'> => ({
  driver_kind: parse_driver_kind(kind),
  manifest: { ...scripted_manifest(kind), ...manifest },
});
const drivers = [
  driver('codex', {
    home: {
      environment_name: 'CODEX_HOME',
      default_path: '~/.codex',
      isolated_directory_name: 'codex',
    },
  }),
  driver('claude', {
    executable: { name: 'claude', override_environment_name: 'PLAN_CLAUDE_EXECUTABLE' },
  }),
  driver('scripted'),
];
function check(
  files: readonly SourceFile[],
  name_allowances: readonly DriverNameAllowance[] = [],
  import_allowances: readonly DriverImportAllowance[] = [],
) {
  return check_driver_names({ files, drivers, name_allowances, import_allowances });
}
const names = (files: readonly SourceFile[]) =>
  check(files).findings.flatMap((finding) =>
    finding.rule === 'name' ? [`${finding.path}:${finding.line}:${finding.driver_kind}`] : [],
  );
const imports = (files: readonly SourceFile[]) =>
  check(files).findings.flatMap((finding) =>
    finding.rule === 'import' ? [`${finding.line}:${finding.target}`] : [],
  );

describe('names', () => {
  it('splits words on separators, camelCase and letter/digit boundaries', () => {
    expect(name_parts('readCodexSnapshot')).toEqual(['read', 'codex', 'snapshot']);
    expect(name_parts('PLAN_CLAUDE_EXECUTABLE')).toEqual(['plan', 'claude', 'executable']);
    expect(name_parts('HTTPCodex2')).toEqual(['http', 'codex', '2']);
    expect(name_parts('claude4_card')).toEqual(['claude', '4', 'card']);
    // An accepted limit: words glued in all capitals stay one part.
    expect(name_parts('CLAUDECODE')).toEqual(['claudecode']);
  });

  it('scans tsx, mts and cts sources', () => {
    expect(
      names(
        ['web/src/Chart.tsx', 'server/a.mts', 'server/b.cts', 'web/src/Chart.test.tsx'].map(
          (path) => ({ path, text: 'const claude4 = 1;' }),
        ),
      ),
    ).toEqual(['web/src/Chart.tsx:1:claude', 'server/a.mts:1:claude', 'server/b.cts:1:claude']);
  });

  it('finds names in identifiers, strings, templates and regular expressions, not comments', () => {
    const text = [
      '// codex in a comment',
      '/* claude in a block */',
      "const key = 'codex';",
      'function read_claude_card() {}',
      'const t = `${x} PLAN_CLAUDE_EXECUTABLE`;',
      'const version = /^codex-cli (\\S+)$/;',
      'const codecs = "codexes";',
    ].join('\n');
    expect(names([{ path: 'server/a.ts', text }])).toEqual([
      'server/a.ts:3:codex',
      'server/a.ts:4:claude',
      'server/a.ts:5:claude',
      'server/a.ts:6:codex',
    ]);
  });

  it('lets a driver package name its own kind and no other', () => {
    const text = "export const codex = 'claude';";
    expect(names([{ path: 'server/drivers/codex/runtime/x.ts', text }])).toEqual([
      'server/drivers/codex/runtime/x.ts:1:claude',
    ]);
  });

  it('bans a fourth driver by its listing alone', () => {
    expect(names([{ path: 'server/b.ts', text: 'const scripted_card = 1;' }])).toEqual([
      'server/b.ts:1:scripted',
    ]);
  });

  it('skips tests, the built-in list and files outside the scanned trees', () => {
    const text = "const x = 'codex';";
    expect(
      names(
        [
          'server/a.test.ts',
          'server/tests/a.ts',
          'server/conversation/testing/a.ts',
          'server/test_host.ts',
          'server/drivers/built_in.ts',
          'src/planner/a.ts',
          'web/tests/a.ts',
        ].map((path) => ({ path, text })),
      ),
    ).toEqual([]);
  });

  it('reads Svelte script and markup without comments, and Swift without comments', () => {
    const svelte = [
      '<script lang="ts">',
      "  const mark = 'claude';",
      '</script>',
      '<!-- codex -->',
      '<span class="codex-mark">x</span>',
    ].join('\n');
    const swift = [
      '// claude',
      'let url = "https://example.com" // codex',
      'let mark = "claude"',
    ].join('\n');
    expect(
      names([
        { path: 'web/src/Mark.svelte', text: svelte },
        { path: 'ios/Panels/Mark.swift', text: swift },
      ]),
    ).toEqual([
      'web/src/Mark.svelte:2:claude',
      'web/src/Mark.svelte:5:codex',
      'ios/Panels/Mark.swift:3:claude',
    ]);
  });
});

describe('allowances', () => {
  const files = [
    { path: 'server/conversation/routes.ts', text: "const keys = ['codex'];" },
    { path: 'server/executor/mac/a.ts', text: "const keys = ['claude'];" },
  ];
  it('covers a file or a directory prefix for one driver kind', () => {
    const report = check(files, [
      {
        path: 'server/conversation/routes.ts',
        driver_kind: 'codex',
        reason: 'r',
        until: 'Core' as const,
      },
      { path: 'server/executor/mac/', driver_kind: 'claude', reason: 'r', until: 'Core' as const },
    ]);
    expect([report.uncovered, report.stale_names]).toEqual([[], []]);
  });
  it('reports a site with no allowance and an allowance with no site', () => {
    const stale = {
      path: 'server/executor/mac/',
      driver_kind: 'codex',
      reason: 'r',
      until: 'Core' as const,
    };
    const report = check(files, [stale]);
    expect(report.uncovered.map((finding) => finding.path)).toEqual([
      'server/conversation/routes.ts',
      'server/executor/mac/a.ts',
    ]);
    expect(report.stale_names).toEqual([stale]);
  });
  it('reports an allowance whose until names no stage', () => {
    const allowance = {
      path: 'server/conversation/routes.ts',
      driver_kind: 'codex',
      reason: 'r',
      until: 'Later' as unknown as 'Core',
    };
    expect(check(files, [allowance]).invalid).toEqual([allowance]);
  });
});

describe('imports', () => {
  it('lets nothing outside server/drivers reach a driver package except through built_in.ts', () => {
    const text = [
      "import { x } from '../drivers/codex/runtime/adapter.ts';",
      "import type { y } from '../drivers/codex/client.ts';",
      "import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';",
      "import type { DriverHost } from '../drivers/core/host.ts';",
      "const z = await import('../drivers/claude/runtime/sdk.ts');",
      "import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';",
    ].join('\n');
    const file = { path: 'server/lifecycle/a.ts', text };
    expect(imports([file])).toEqual([
      '1:server/drivers/codex/runtime/adapter.ts',
      '2:server/drivers/codex/client.ts',
      '5:server/drivers/claude/runtime/sdk.ts',
    ]);
    // The specifier is an import finding, not also a name finding.
    expect(names([file])).toEqual([]);
    const allowance = (driver_kind: string) => ({
      path: 'server/lifecycle/a.ts',
      driver_kind,
      reason: 'r',
      until: 'Core' as const,
    });
    expect(check([file], [], [allowance('codex'), allowance('claude')]).uncovered).toEqual([]);
    // An allowance covers one driver package only.
    expect(
      check([file], [], [allowance('codex')]).uncovered.map((finding) => finding.line),
    ).toEqual([5]);
    expect(check([], [], [allowance('codex')]).stale_imports).toEqual([allowance('codex')]);
  });

  it('keeps drivers/core and each package out of the other packages', () => {
    expect(
      imports([
        { path: 'server/drivers/core/a.ts', text: "import { x } from '../codex/runtime/a.ts';" },
        {
          path: 'server/drivers/claude/runtime/b.ts',
          text: "import { y } from '../../codex/runtime/a.ts';\nimport { z } from './c.ts';",
        },
      ]),
    ).toEqual(['1:server/drivers/codex/runtime/a.ts', '1:server/drivers/codex/runtime/a.ts']);
  });

  it('records require calls, import-equals and computed paths', () => {
    const text = [
      "import { createRequire } from 'node:module';",
      "import a = require('../drivers/codex/runtime/a.ts');",
      "const b = createRequire(import.meta.url)('../drivers/claude/runtime/b.ts');",
      'const require = createRequire(import.meta.url);',
      "const c = require.resolve('../drivers/scripted/runtime/c.ts');",
      'const d = await import(`../drivers/${kind}/runtime/adapter.ts`);',
      'const e = await import(config.module_url);',
    ].join('\n');
    expect(imports([{ path: 'server/lifecycle/a.ts', text }])).toEqual([
      '2:server/drivers/codex/runtime/a.ts',
      '3:server/drivers/claude/runtime/b.ts',
      '5:server/drivers/scripted/runtime/c.ts',
      '6:(computed)',
    ]);
    // Without createRequire, a function named require is not a module load.
    const local = 'function require(ok: boolean) {}\nrequire(x);';
    expect(imports([{ path: 'server/tickets/a.ts', text: local }])).toEqual([]);
  });

  it('keeps driver.ts, manifest.ts, client.ts, models.ts and tool_detail.ts free of runtime value imports', () => {
    const driver_text = [
      "import { NO_DRIVER_CONFIG } from '../core/driver.ts';",
      "import { codex_manifest } from './manifest.ts';",
      "import type { CodexChildLaunch } from './runtime/adapter.ts';",
      "import { launch } from './runtime/adapter.ts';",
      "const loaded = await import('./runtime/instance.ts');",
      "import { type Launch } from './runtime/adapter.ts';",
      "import { query } from '@anthropic-ai/claude-agent-sdk';",
      "import type { Options } from '@anthropic-ai/claude-agent-sdk';",
      'const computed = await import(`./runtime/${name}.ts`);',
      "import { codex_models } from './models.ts';",
      "import { codex_readable_tool_call_detail } from './tool_detail.ts';",
    ].join('\n');
    const manifest_text = [
      "import { x } from '../../core/config.ts';",
      "const loaded = await import('./runtime/instance.ts');",
      "import { readFileSync } from 'node:fs';",
      'const computed = await import(path);',
    ].join('\n');
    expect(
      imports([
        { path: 'server/drivers/codex/driver.ts', text: driver_text },
        { path: 'server/drivers/codex/manifest.ts', text: manifest_text },
        {
          path: 'server/drivers/codex/models.ts',
          text: "import { parse } from './runtime/catalog.ts';",
        },
        {
          path: 'server/drivers/codex/tool_detail.ts',
          text: [
            "import { is_record } from '../../core/records.ts';",
            "import { parse } from './runtime/events.ts';",
          ].join('\n'),
        },
      ]),
    ).toEqual([
      '4:server/drivers/codex/runtime/adapter.ts',
      // `import { type X }` still loads the module.
      '6:server/drivers/codex/runtime/adapter.ts',
      '7:@anthropic-ai/claude-agent-sdk',
      '1:server/core/config.ts',
      '2:server/drivers/codex/runtime/instance.ts',
      '3:node:fs',
      '4:(computed)',
      '1:server/drivers/codex/runtime/catalog.ts',
      '2:server/drivers/codex/runtime/events.ts',
    ]);
  });

  it('keeps packages out of drivers/core', () => {
    const text = "import ts from 'typescript';\nimport { join } from 'node:path';";
    expect(imports([{ path: 'server/drivers/core/a.ts', text }])).toEqual(['1:typescript']);
  });
});
