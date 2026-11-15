/** Hosted build entry. GitHub artifact identities belong to the publishing workflow. */
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { with_publication_entry } from './publication_entry.ts';
import { build_mac_home } from './mac_home_build.ts';
import { prove_mac_home } from './mac_home_proof.ts';

export function mac_home_main(args: string[]): Promise<0 | 1> {
  return with_publication_entry(async (execution, register_output) => {
    const names = ['source', 'revision', 'output', 'node-modules', 'agent-backends', 'web-dist', 'public-cli-entrypoint'] as const;
    const { values } = parseArgs({ args, options: Object.fromEntries(names.map((name) => [name, { type: 'string' as const }])), strict: true });
    const text = (name: typeof names[number]): string => {
      const value = values[name];
      if (typeof value !== 'string' || !value) throw new Error(`Required option: --${name}`);
      return value;
    };
    const result = await build_mac_home({ source_root: text('source'), requested_sha: text('revision'), output_directory: text('output'), production_node_modules: text('node-modules'), agent_backends_root: text('agent-backends'), web_dist: text('web-dist') }, { public_cli_entrypoint: text('public-cli-entrypoint') }, execution);
    const proved = await prove_mac_home(result, text('source'), text('output'), execution);
    register_output(text('output'));
    process.stdout.write(JSON.stringify(proved, null, 2) + '\n');
  });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = await mac_home_main(process.argv.slice(2));
