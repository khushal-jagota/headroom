/** The fixed first-machine service reads its registered identity without shell interpolation. */
import { readFile } from 'node:fs/promises';
import { valid_uuid } from '../execution_identity.ts';
import { main } from '../cli/main.ts';

try {
  if (process.argv.length !== 5)
    throw new Error('Expected machine identity, revision, and server link');
  const identity = JSON.parse(await readFile(process.argv[2]!, 'utf8')) as { machine_id?: unknown };
  if (typeof identity.machine_id !== 'string' || !valid_uuid(identity.machine_id))
    throw new Error('The first machine identity is invalid');
  process.argv = [
    process.argv[0]!,
    process.argv[1]!,
    'executor',
    'run',
    '--executor-id',
    identity.machine_id,
    '--application-revision',
    process.argv[3]!,
    '--panels-server-url',
    process.argv[4]!,
  ];
  await main();
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
