/** Scripted provider setup for the hosted cross-version machine proof. */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

/** Historical Linux runtimes support this provider's explicit executable setting. */
export const SCRIPTED_MACHINE_BACKEND = 'claude';

export async function prepare_machine_providers(input: {
  root: string;
  home: string;
  node: string;
  label: string;
  source: string;
}): Promise<{ bin: string; environment: Record<string, string> }> {
  const { root, home, node, label, source } = input;
  const bin = join(home, '.local/bin');
  await mkdir(bin, { recursive: true });
  for (const [name, script, extra] of [
    [
      'claude',
      'claude/testing/scripted_cli.ts',
      {
        UNIT18_TRACE: join(root, label + '-claude.jsonl'),
        UNIT18_WRAPPED_FILE: join(root, label + '-claude-launch.json'),
      },
    ],
    [
      'codex',
      'codex/testing/scripted_app_server.ts',
      { UNIT17_TRACE: join(root, label + '-codex.jsonl'), UNIT17_CORE_PROOF: '1' },
    ],
  ] as const)
    await writeFile(
      join(bin, name),
      `#!${node}\nObject.assign(process.env, ${JSON.stringify(extra)});\nawait import(${JSON.stringify(join(source, 'server/drivers', script))});\n`,
      { mode: 0o755 },
    );
  await writeFile(join(root, label + '-claude-launch.json'), '{}');
  const environment = {
    PLAN_CLAUDE_EXECUTABLE: join(bin, 'claude'),
    CODEX_HOME: join(home, '.codex'),
    CLAUDE_CONFIG_DIR: join(home, '.claude'),
  };
  for (const path of [environment.CODEX_HOME, environment.CLAUDE_CONFIG_DIR])
    await mkdir(join(path, 'skills'), { recursive: true });
  return { bin, environment };
}
