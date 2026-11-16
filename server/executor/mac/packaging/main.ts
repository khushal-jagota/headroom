/** Explicit package construction command. It only writes a new output directory. */
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { build_mac_package } from './build.ts';

export async function main(args: string[]): Promise<void> {
  const names = [
    'source',
    'node-distribution',
    'node-modules',
    'output',
    'revision',
    'home',
    'executor-id',
    'server-url',
    'backend-path',
  ] as const;
  const { values } = parseArgs({
    args,
    options: Object.fromEntries(names.map((name) => [name, { type: 'string' as const }])),
    strict: true,
  });
  for (const name of names)
    if (typeof values[name] !== 'string' || !values[name])
      throw new Error(`Required option: --${name}`);
  const string = (name: (typeof names)[number]) => values[name] as string;
  const result = await build_mac_package({
    source_root: string('source'),
    node_distribution: string('node-distribution'),
    production_node_modules: string('node-modules'),
    output: string('output'),
    revision: string('revision'),
    home: string('home'),
    executor_id: string('executor-id'),
    server_url: string('server-url'),
    backend_path: string('backend-path'),
  });
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    await main(process.argv.slice(2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
